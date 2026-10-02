"""조치 실행 서비스 (M3-02) — 가드레일 → 실행기 → 이력. 모든 조치는 이 경로로만 실행된다."""

import logging

from aiops.remediation.actions import ActionResult, RemediationAction
from aiops.remediation.executors import Executor
from aiops.remediation.guardrails import Guardrails, GuardrailViolation

logger = logging.getLogger(__name__)


class RemediationService:
    def __init__(self, executor: Executor, guardrails: Guardrails | None = None) -> None:
        self.executor = executor
        self.guardrails = guardrails or Guardrails()

    async def execute(
        self, action: RemediationAction, *, dry_run: bool = False, approved_by: str | None = None
    ) -> ActionResult:
        try:
            current = await self.executor.current_replicas(action)
            self.guardrails.check(action, current)
        except GuardrailViolation as exc:
            logger.warning("guardrail blocked %s: %s", action.describe(), exc)
            return ActionResult(
                action_id=action.id, ok=False, dry_run=dry_run, detail=f"가드레일 차단: {exc}"
            )
        if not dry_run and approved_by is None and action.risk != "none":
            return ActionResult(
                action_id=action.id,
                ok=False,
                dry_run=False,
                detail="승인자 없이 상태 변경 조치는 실행할 수 없음",
            )
        try:
            result = await self.executor.execute(action, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - 실행 실패는 결과로 보고(워크플로우가 판단)
            return ActionResult(
                action_id=action.id, ok=False, dry_run=dry_run, detail=f"실행 실패: {exc!r}"
            )
        if result.ok and not dry_run:
            self.guardrails.record(action)  # 실제 실행만 시간당 한도·쿨다운에 반영
        return result
