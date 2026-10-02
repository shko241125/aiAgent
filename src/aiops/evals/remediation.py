"""조치 평가 (M3-03) — RCA → 플레이북 → (승인 가정) 실행 → 효과 검증, 시나리오별 정답과 비교.

지표
- plan_accuracy : 1차 조치 종류가 정답(expected_action)과 같은 비율
- recovery_rate : 자동화 대상 시나리오에서 실행 후 실제 복구된 비율
- safe_manual   : 자동화하면 안 되는 시나리오(manual)에서 상태 변경 없이 사람에게 넘긴 비율
"""

from pydantic import BaseModel

from aiops.analytics.rca import RCAAnalyzer
from aiops.integrations.simulated import FaultScenario, SimulatedOpsSource
from aiops.remediation.executors import SimulatedExecutor
from aiops.remediation.guardrails import GuardrailPolicy, Guardrails
from aiops.remediation.playbook import plan_actions
from aiops.remediation.runner import remediate
from aiops.remediation.service import RemediationService


class RemediationCase(BaseModel):
    id: str
    expected: str
    planned: str | None
    status: str
    correct_plan: bool


class RemediationReport(BaseModel):
    plan_accuracy: float
    recovery_rate: float
    safe_manual: float
    cases: list[RemediationCase]


async def _noop_sleep(_: float) -> None:
    return None


async def evaluate_remediation(scenarios: list[FaultScenario], seed: int = 7) -> RemediationReport:
    cases = []
    for sc in scenarios:
        source = SimulatedOpsSource(seed=seed)
        source.apply_scenario(sc.model_copy(deep=True))
        plan = plan_actions(await RCAAnalyzer(source).analyze(sc.alert_service))
        primary = plan[0] if plan else None
        status = "no_plan"
        if primary is not None:
            svc = RemediationService(
                SimulatedExecutor(source), Guardrails(GuardrailPolicy(denied_services=[]))
            )
            outcome = await remediate(
                svc,
                source,
                primary,
                approved_by="eval",
                verify_services=[sc.alert_service, primary.service],
                verify_attempts=1,
                sleep=_noop_sleep,
            )
            status = outcome.status
        planned = primary.type.value if primary else None
        cases.append(
            RemediationCase(
                id=sc.id,
                expected=sc.expected_action or "",
                planned=planned,
                status=status,
                correct_plan=planned == sc.expected_action,
            )
        )
    auto = [c for c in cases if c.expected != "manual"]
    manual = [c for c in cases if c.expected == "manual"]
    return RemediationReport(
        plan_accuracy=round(sum(c.correct_plan for c in cases) / max(len(cases), 1), 3),
        recovery_rate=round(sum(c.status == "recovered" for c in auto) / max(len(auto), 1), 3),
        safe_manual=round(sum(c.status == "manual" for c in manual) / max(len(manual), 1), 3),
        cases=cases,
    )
