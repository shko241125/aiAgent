"""프롬프트/에이전트 평가 러너 (M1-07 / 4.3).

시나리오 = (에이전트, 입력, 시뮬레이터 장애 시나리오, 기대 조건들)
- scripted 모드: 시나리오에 적힌 '모범 응답'으로 LLM 을 대체
                 → 프롬프트 렌더링·컨텍스트 주입·파싱·스키마 경로를 CI 에서 회귀 검사
                 (모델 품질이 아니라 배관을 검사)
- live 모드   : 실제 모델로 실행 → 같은 체크로 모델·프롬프트 품질을 측정 (M1-10)
결과에는 프롬프트 이름·버전이 남아, 프롬프트를 바꾸면 무엇이 좋아지고 나빠졌는지 비교할 수 있다.
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aiops.agents.base import AgentResult, AgentTask, LLMAgent
from aiops.agents.context import AgentContext
from aiops.integrations.simulated import FaultScenario
from aiops.llm.base import ChatMessage, LLMProvider, LLMResponse
from aiops.llm.providers.fake import FakeLLMProvider

CITE_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]+)\]")


class Check(BaseModel):
    type: Literal[
        "schema_valid",
        "field_equals",
        "field_in",
        "contains",
        "not_contains",
        "cites",
        "prompt_contains",
    ]
    field: str | None = None
    value: Any = None
    values: list[Any] = Field(default_factory=list)


class PromptScenario(BaseModel):
    id: str
    agent: str
    instruction: str = "주어진 상황을 분석하라"
    inputs: dict[str, Any] = Field(default_factory=dict)
    blackboard: dict[str, Any] = Field(default_factory=dict)
    fault_scenario: str | None = None  # data/eval/rca_scenarios.json 의 id
    scripted: list[str | dict] = Field(default_factory=list)  # 문자열 또는 LLMResponse dict
    checks: list[Check]


class CheckResult(BaseModel):
    type: str
    ok: bool
    detail: str = ""


class ScenarioResult(BaseModel):
    id: str
    agent: str
    prompt: str
    prompt_version: str
    passed: bool
    checks: list[CheckResult]
    output: str
    data: dict[str, Any]
    latency_ms: float
    usage: dict[str, int] = Field(default_factory=dict)


class EvalSuiteReport(BaseModel):
    mode: str
    passed: int
    total: int
    results: list[ScenarioResult]


class RecordingLLM(LLMProvider):
    """LLM 호출을 가로채 기록 — 렌더링된 프롬프트와 토큰 사용량을 검사하기 위함."""

    name = "recording"

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.calls: list[list[ChatMessage]] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls.append(list(messages))
        resp = await self.inner.chat(messages, **kwargs)
        for k in self.usage:
            self.usage[k] += resp.usage.get(k, 0)
        return resp


def _get(data: dict, path: str | None) -> Any:
    cur: Any = data
    for part in (path or "").split("."):
        if not part:
            continue
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def run_checks(
    checks: list[Check], res: AgentResult, prompt_text: str, known_docs: set[str]
) -> list[CheckResult]:
    out = []
    text = res.output or ""
    for c in checks:
        match c.type:
            case "schema_valid":
                ok = bool(res.data) and not res.schema_errors
                out.append(CheckResult(type=c.type, ok=ok, detail="; ".join(res.schema_errors)))
            case "field_equals":
                got = _get(res.data, c.field)
                out.append(
                    CheckResult(
                        type=c.type,
                        ok=got == c.value,
                        detail=f"{c.field}={got!r} (기대 {c.value!r})",
                    )
                )
            case "field_in":
                got = _get(res.data, c.field)
                out.append(
                    CheckResult(
                        type=c.type,
                        ok=got in c.values,
                        detail=f"{c.field}={got!r} (허용 {c.values})",
                    )
                )
            case "contains" | "not_contains":
                hits = [v for v in c.values if str(v).lower() in text.lower()]
                ok = len(hits) == len(c.values) if c.type == "contains" else not hits
                out.append(CheckResult(type=c.type, ok=ok, detail=f"일치: {hits}"))
            case "cites":
                cited = set(CITE_RE.findall(text))
                valid = cited & known_docs
                out.append(
                    CheckResult(
                        type=c.type,
                        ok=len(valid) >= int(c.value or 1),
                        detail=f"유효 인용 {sorted(valid)} / 무효 {sorted(cited - valid)}",
                    )
                )
            case "prompt_contains":
                hits = [v for v in c.values if str(v) in prompt_text]
                out.append(
                    CheckResult(
                        type=c.type, ok=len(hits) == len(c.values), detail=f"프롬프트 포함: {hits}"
                    )
                )
    return out


def _scripted(items: list[str | dict]) -> list[LLMResponse]:
    """모범 응답: 문자열은 최종 답변, dict 는 LLMResponse (도구 호출 포함 가능)."""
    return [
        LLMResponse(content=r) if isinstance(r, str) else LLMResponse.model_validate(r)
        for r in items
    ]


def load_prompt_scenarios(path: Path) -> list[PromptScenario]:
    return [PromptScenario.model_validate(s) for s in json.loads(path.read_text(encoding="utf-8"))]


async def run_suite(
    platform,
    scenarios: list[PromptScenario],
    fault_scenarios: dict[str, FaultScenario],
    mode: Literal["scripted", "live"] = "scripted",
) -> EvalSuiteReport:
    known_docs = set(platform.rag.documents())
    results = []
    for sc in scenarios:
        agent: LLMAgent = platform.agents.get(sc.agent)
        if sc.fault_scenario:
            platform.source.apply_scenario(fault_scenarios[sc.fault_scenario])
        else:
            platform.source.apply_scenario(FaultScenario(id="calm", alert_service="-"))
        base = FakeLLMProvider(_scripted(sc.scripted)) if mode == "scripted" else agent.llm
        recorder = RecordingLLM(base)
        original, agent.llm = agent.llm, recorder
        ctx = AgentContext()
        for k, v in sc.blackboard.items():
            ctx.blackboard.write(k, v, author="eval")
        started = time.perf_counter()
        try:
            res = await agent.run(AgentTask(instruction=sc.instruction, inputs=sc.inputs), ctx)
        finally:
            agent.llm = original
        prompt_text = (
            "\n".join(m.content or "" for m in recorder.calls[0]) if recorder.calls else ""
        )
        checks = run_checks(sc.checks, res, prompt_text, known_docs)
        tmpl = platform.prompts.get(agent.prompt_name)
        results.append(
            ScenarioResult(
                id=sc.id,
                agent=sc.agent,
                prompt=tmpl.name,
                prompt_version=tmpl.version,
                passed=all(c.ok for c in checks),
                checks=checks,
                output=res.output[:2000],
                data=res.data,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                usage=recorder.usage,
            )
        )
    return EvalSuiteReport(
        mode=mode, passed=sum(r.passed for r in results), total=len(results), results=results
    )
