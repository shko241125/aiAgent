"""조치 실행 → 검증 → (실패 시) 되돌림·에스컬레이션 (M3-03 / 2.3).

승인은 '1차 조치' 하나에 대해 받는다. 복구되지 않으면 대안을 자동으로 시도하지 않는다 —
승인받지 않은 조치를 연쇄 실행하지 않기 위함. 되돌릴 수 있는 조치는 되돌리고 사람에게 넘긴다.
"""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from aiops.integrations.base import OpsSource
from aiops.remediation.actions import ActionResult, ActionType, RemediationAction
from aiops.remediation.service import RemediationService
from aiops.remediation.verify import VerificationResult, verify_recovery


class RemediationOutcome(BaseModel):
    status: str  # recovered | not_recovered | blocked | manual
    action: RemediationAction
    result: ActionResult | None = None
    verification: VerificationResult | None = None
    reverted: ActionResult | None = None
    escalate: bool = False
    notes: list[str] = Field(default_factory=list)


async def remediate(
    service: RemediationService,
    source: OpsSource,
    action: RemediationAction,
    *,
    approved_by: str,
    verify_services: list[str],
    verify_attempts: int = 3,
    verify_interval_s: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> RemediationOutcome:
    if action.type == ActionType.MANUAL:
        return RemediationOutcome(
            status="manual",
            action=action,
            escalate=True,
            notes=[f"자동화 불가 — 담당자 조치 필요: {action.reason}"],
        )
    result = await service.execute(action, approved_by=approved_by)
    if not result.ok:
        return RemediationOutcome(
            status="blocked", action=action, result=result, escalate=True, notes=[result.detail]
        )
    kw = {"sleep": sleep} if sleep else {}
    verification = await verify_recovery(
        source, verify_services, attempts=verify_attempts, interval_s=verify_interval_s, **kw
    )
    if verification.recovered:
        return RemediationOutcome(
            status="recovered", action=action, result=result, verification=verification
        )
    outcome = RemediationOutcome(
        status="not_recovered",
        action=action,
        result=result,
        verification=verification,
        escalate=True,
        notes=["조치 후에도 복구되지 않음"],
    )
    if result.revert is not None:  # 효과 없는 변경은 원상 복구 (상태를 더 나쁘게 두지 않는다)
        outcome.reverted = await service.execute(
            result.revert, approved_by=f"{approved_by}(auto-revert)", revert=True
        )
        outcome.notes.append(f"되돌림: {outcome.reverted.detail}")
    return outcome
