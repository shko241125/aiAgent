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
from aiops.analytics.anomaly.ensemble import default_detector
from aiops.analytics.insights import summarize_series
from aiops.analytics.situation import SignalSet, assess
from aiops.domain.models import utcnow
from aiops.integrations.simulated import SimulatedOpsSource

DEFAULT_METRICS = ["cpu_usage", "memory_usage", "latency_p95_ms", "error_rate"]


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, indent=2, default=str)


class DetectionAgent(LLMAgent):
    """2.1 장애 탐지 및 분석 — 알람 수신 시 메트릭 이상 탐지 + 상황 인식 후 장애 여부 판단."""

    name = "detection"
    description = "알람과 메트릭을 분석해 실제 장애 여부·심각도·영향 범위를 판단한다"
    prompt_name = "detection"
    tool_names = ["query_metrics", "search_logs", "get_service_dependencies"]

    def __init__(self, *args: Any, source: SimulatedOpsSource, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.source = source

    async def build_input(self, task: AgentTask, ctx: AgentContext) -> dict[str, Any]:
        alert = task.inputs.get("alert", {})
        service = alert.get("service") or task.inputs.get("service", "unknown")
        end = utcnow()
        facts, anomalies_by_metric, length = {}, {}, 1
        for metric in DEFAULT_METRICS:
            series = await self.source.query_range(service, metric, end - timedelta(hours=1), end)
            anomalies = default_detector().detect(series.values)
            anomalies_by_metric[metric] = anomalies
            facts[metric] = summarize_series(series.values, anomalies)
            length = len(series.values)
        changes = await self.source.list_events(end - timedelta(hours=2), end, service)
        deps = await self.source.dependencies(service)
        situation = assess(
            SignalSet(
                service=service,
                anomalies=anomalies_by_metric,
                series_length=length,
                recent_changes=sum(1 for e in changes if e.type == "deploy"),
                downstream_impacted=len(deps.get("upstream", [])),
            )
        )
        ctx.blackboard.write("detection.situation", situation.model_dump(), author=self.name)
        ctx.blackboard.write("service", service, author=self.name)
        return {
            "alert": _dump(alert),
            "situation": _dump(situation.model_dump()),
            "facts": _dump(facts),
            "input": task.instruction,
        }


class RCAAgent(LLMAgent):
    """2.2 근본 원인 분석."""

    name = "rca"
    description = "탐지 결과·로그·변경이력·의존성·지식베이스로 근본 원인을 추론한다"
    prompt_name = "rca"
    tool_names = [
        "query_metrics",
        "search_logs",
        "get_recent_changes",
        "get_service_dependencies",
        "search_knowledge",
    ]

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
        return {"detection": _dump(detection), "service": service, "input": task.instruction}


class RemediationAgent(LLMAgent):
    """2.3 운영 자동화 — 조치 계획 수립 및 (승인 정책 하) 실행."""

    name = "remediation"
    description = "RCA 결과로 복구 조치를 계획하고, 승인 정책에 따라 자동 실행한다"
    prompt_name = "remediation"
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
