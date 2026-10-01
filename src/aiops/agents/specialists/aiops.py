"""AIOps 특화 에이전트 (2.1 ~ 2.6).

각 에이전트 = 프롬프트 + 허용 도구 + (선택) 결정적 분석 전처리.
수치 분석(이상 탐지·상황 인식)은 코드가 먼저 수행하고, LLM 은 해석·판단·계획에 집중한다.
"""

import json
from datetime import timedelta
from typing import Any

from aiops.agents.base import AgentResult, AgentTask, LLMAgent
from aiops.agents.context import AgentContext
from aiops.agents.memory.base import MemoryItem
from aiops.agents.schemas import DetectionOutput, RCAOutput, RemediationOutput
from aiops.analytics.anomaly.ensemble import default_detector
from aiops.analytics.insights import summarize_series
from aiops.analytics.rca import RCAAnalyzer
from aiops.analytics.situation import assess
from aiops.analytics.situation_model import (
    LogisticModel,
    assess_learned,
    featurize,
    propagate_risk,
    rule_signals,
)
from aiops.domain.models import Severity, utcnow
from aiops.integrations.base import OpsSource
from aiops.rag.citations import validate_citations

DEFAULT_METRICS = ["cpu_usage", "memory_usage", "latency_p95_ms", "error_rate"]


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, indent=2, default=str)


class DetectionAgent(LLMAgent):
    """2.1 장애 탐지 및 분석 — 알람 수신 시 근거 수집 + 상황 인식 후 장애 여부 판단.

    M2-07: 평가에 쓴 것과 같은 근거 경로(RCAAnalyzer.collect)로 규칙·학습 판정과
    하위 의존성 위험 전파를 계산해 LLM 에 준다. 학습 확률이 트리아지 임계치 미만이면
    LLM 을 호출하지 않고 '장애 아님'으로 결정한다 (비용·지연 절감).
    """

    name = "detection"
    description = "알람과 메트릭을 분석해 실제 장애 여부·심각도·영향 범위를 판단한다"
    prompt_name = "detection"
    output_model = DetectionOutput
    tool_names = ["query_metrics", "search_logs", "get_service_dependencies"]

    def __init__(
        self,
        *args: Any,
        source: OpsSource,
        situation_model: LogisticModel | None = None,
        triage_threshold: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.source = source
        self.analyzer = RCAAnalyzer(source)
        self.situation_model = situation_model
        self.triage_threshold = triage_threshold

    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        alert = task.inputs.get("alert", {})
        service = alert.get("service") or task.inputs.get("service", "unknown")
        end = utcnow()
        facts = {}
        for metric in DEFAULT_METRICS:
            series = await self.source.query_range(service, metric, end - timedelta(hours=1), end)
            facts[metric] = summarize_series(
                series.values, default_detector().detect(series.values)
            )

        evidence = await self.analyzer.collect(service)
        rule = assess(rule_signals(evidence))
        situation: dict[str, Any] = {"rule": rule.model_dump()}
        own_risk = rule.risk_score / 100
        if self.situation_model is not None:
            learned = assess_learned(self.situation_model, service, evidence)
            situation["learned"] = learned.model_dump()
            own_risk = self.situation_model.predict_proba(featurize(evidence))  # 반올림 전 값
        # 하위 의존성 위험 전파: 내 지표가 멀쩡해도 하위가 무너지면 나도 위험하다
        risk = {e.service: min(1.0, len(e.anomalous_metrics) / 2) for e in evidence}
        risk[service] = own_risk
        downstream = {
            e.service: (await self.source.dependencies(e.service))["downstream"] for e in evidence
        }
        propagated = propagate_risk(risk, downstream)[service]
        ctx.blackboard.write(
            "detection.risk", {"own": own_risk, "propagated": propagated}, author=self.name
        )
        situation["propagated_risk"] = round(propagated, 3)
        situation["anomalous_services"] = {
            e.service: sorted(e.anomalous_metrics) for e in evidence if e.anomalous_metrics
        }

        ctx.blackboard.write("detection.situation", situation, author=self.name)
        ctx.blackboard.write("service", service, author=self.name)
        return {
            "alert": _dump(alert),
            "situation": _dump(situation),
            "facts": _dump(facts),
            "input": task.instruction,
        }

    async def pre_decide(
        self, task: AgentTask, ctx: AgentContext, variables: dict[str, Any]
    ) -> AgentResult | None:
        sit = ctx.blackboard.read("detection.situation") or {}
        risk = ctx.blackboard.read("detection.risk") or {}
        learned = sit.get("learned")
        if not learned or self.triage_threshold <= 0:
            return None
        p = risk.get("own", 1.0)
        if max(p, risk.get("propagated", 1.0)) >= self.triage_threshold:  # 원값으로 비교
            return None
        out = DetectionOutput(
            is_incident=False,
            severity=Severity.INFO,
            summary=f"트리아지: 장애 확률 {p:.3f} < {self.triage_threshold} "
            f"(근거: {learned['factors']}) — LLM 판단 생략",
        )
        return AgentResult(agent=self.name, output=out.summary, data=out.model_dump(mode="json"))


class RCAAgent(LLMAgent):
    """2.2 근본 원인 분석."""

    name = "rca"
    description = "탐지 결과·로그·변경이력·의존성·지식베이스로 근본 원인을 추론한다"
    prompt_name = "rca"
    output_model = RCAOutput
    tool_names = [
        "rank_root_causes",
        "summarize_service_logs",
        "query_metrics",
        "search_logs",
        "get_recent_changes",
        "get_service_dependencies",
        "search_knowledge",
    ]

    def __init__(self, *args: Any, source: OpsSource | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.analyzer = RCAAnalyzer(source) if source is not None else None

    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        detection = {
            "output": ctx.blackboard.read("detection.output"),
            "data": ctx.blackboard.read("detection.data"),
            "situation": ctx.blackboard.read("detection.situation"),
        }
        service = ctx.blackboard.read("service") or task.inputs.get("service", "unknown")
        similar: list[str] = []
        if ctx.long_term:  # 장기 기억: 같은 서비스의 과거 인시던트 요약
            items = await ctx.long_term.search(f"service:{service}", task.instruction, k=3)
            similar = [i.content for i in items]
        detection["similar_past_incidents"] = similar
        candidates = "(분석 엔진 미연결)"
        if self.analyzer is not None:  # M1-08: 코드가 먼저 근거 있는 후보를 뽑는다
            ranked = await self.analyzer.analyze(service)
            ctx.blackboard.write(
                "rca.candidates", [c.model_dump() for c in ranked], author=self.name
            )
            candidates = (
                "\n".join(
                    f'{i}) {c.service}/{c.kind} {c.score:.2f} "{c.cause}" 근거={c.evidence}'
                    + (f" runbook=[{c.runbook}]" if c.runbook else "")
                    for i, c in enumerate(ranked, 1)
                )
                or "(후보 없음 — 도구로 근거를 직접 수집하십시오)"
            )
        return {
            "detection": _dump(detection),
            "service": service,
            "candidates": candidates,
            "input": task.instruction,
        }


class RemediationAgent(LLMAgent):
    """2.3 운영 자동화 — 조치 계획 수립 및 (승인 정책 하) 실행."""

    name = "remediation"
    description = "RCA 결과로 복구 조치를 계획하고, 승인 정책에 따라 자동 실행한다"
    prompt_name = "remediation"
    output_model = RemediationOutput
    tool_names = [
        "restart_service",
        "scale_service",
        "rollback_deployment",
        "search_knowledge",
        "query_metrics",
    ]

    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        rca = {"output": ctx.blackboard.read("rca.output"), "data": ctx.blackboard.read("rca.data")}
        service = ctx.blackboard.read("service") or task.inputs.get("service", "unknown")
        return {"rca": _dump(rca), "service": service, "input": task.instruction}


class IncidentAgent(LLMAgent):
    """2.4 인시던트 관리 — 기록 갱신·상황 공유.

    TODO(2.4): ITSM(ServiceNow/Jira) 티켓 연동, 온콜 호출·에스컬레이션.
    """

    name = "incident"
    description = "탐지·RCA·조치 결과를 종합해 인시던트 기록과 상황 공유 메시지를 작성한다"
    prompt_name = "incident"
    tool_names: list[str] = []

    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        bb = ctx.blackboard
        return {
            "detection": bb.read("detection.output", ""),
            "rca": bb.read("rca.output", ""),
            "remediation": bb.read("remediation.output", ""),
            "input": task.instruction,
        }

    async def post_process(self, result: AgentResult, ctx: AgentContext) -> AgentResult:
        result = await super().post_process(result, ctx)
        if ctx.long_term:
            service = ctx.blackboard.read("service", "unknown")
            await ctx.long_term.add(
                MemoryItem(
                    namespace=f"service:{service}",
                    content=result.output[:2000],
                    metadata={"incident_id": ctx.incident_id, "run_id": ctx.run_id},
                )
            )
        return result


class ReportAgent(LLMAgent):
    """2.5 운영 보고서 생성 — 포스트모템/일간·주간 운영 리포트."""

    name = "report"
    description = "인시던트·운영 데이터를 바탕으로 Markdown 운영 보고서를 작성한다"
    prompt_name = "report"
    tool_names = ["get_recent_changes", "query_metrics"]

    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        collected = {k: v for k, v in ctx.blackboard.data.items() if k.endswith(".output")}
        body = task.instruction + ("\n\n[수집된 결과]\n" + _dump(collected) if collected else "")
        return {"input": body}


class KnowledgeAgent(LLMAgent):
    """2.6 운영 지식 기반 Q&A — RAG 도구를 사용하는 에이전트형 RAG(Agentic RAG)."""

    name = "knowledge"
    description = "runbook·과거 장애 기록을 검색해 근거 기반으로 답한다"
    prompt_name = "knowledge"
    tool_names = ["search_knowledge"]

    async def post_process(self, result: AgentResult, ctx: AgentContext) -> AgentResult:
        """실제로 검색해 본 문서만 유효 인용으로 인정 (M1-09) — 환각 인용을 드러낸다."""
        seen = {
            item["doc_id"]
            for tr in result.tool_results
            if tr.ok and tr.name == "search_knowledge"
            for item in (tr.output or [])
        }
        report = validate_citations(result.output, seen)
        result.data = {**result.data, "citations": report.model_dump()}
        ctx.log(self.name, "citations", rate=report.citation_rate, invalid=report.invalid)
        return await super().post_process(result, ctx)
