"""사람 승인 (M3-05 / 2.3) — 워크플로우 승인 단계와 사람을 잇는다.

엔진(approval 단계) ─request─▶ SqlApprovalGate ─▶ DB + Notifier(Slack 등)
사람(API·Slack 버튼) ─decide─▶ ApprovalService ─▶ DB ─on_decided─▶ 워크플로우 resume
sweep(주기 실행): 미응답 → 에스컬레이션, 만료 → 자동 거부(안전한 기본값) → resume
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiops.db.models import ApprovalRow
from aiops.domain.models import utcnow
from aiops.observability.metrics import APPROVAL_DECISIONS, APPROVAL_LATENCY
from aiops.services.notify import LogNotifier, Notifier
from aiops.workflow.engine import ApprovalDecision, ApprovalGate, WorkflowRun


class Approval(BaseModel):
    id: str
    run_id: str
    step_id: str
    incident_id: str | None
    status: str
    details: dict[str, Any]
    requested_at: datetime
    expires_at: datetime
    escalated_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    reason: str = ""

    @property
    def summary(self) -> str:
        return self.details.get("summary", "")


class ApprovalConflict(RuntimeError):
    pass


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite 는 tz 를 버린다


def _to_model(r: ApprovalRow) -> Approval:
    data = {c: getattr(r, c) for c in Approval.model_fields}
    for k in ("requested_at", "expires_at", "escalated_at", "decided_at"):
        data[k] = _aware(data[k])
    return Approval.model_validate(data)


def approval_id(run_id: str, step_id: str) -> str:
    return f"apr-{run_id}-{step_id}"


class ApprovalService(ApprovalGate):
    """엔진의 ApprovalGate 이자 사람 쪽 결정 API."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        notifier: Notifier | None = None,
        *,
        escalate_after: timedelta = timedelta(minutes=15),
        timeout: timedelta = timedelta(minutes=60),
        clock: Callable[[], datetime] = utcnow,
        record: Callable[..., Awaitable[Any]] | None = None,  # 타임라인 기록 훅
    ) -> None:
        self.sessionmaker = sessionmaker
        self.notifier = notifier or LogNotifier()
        self.escalate_after = escalate_after
        self.timeout = timeout
        self.clock = clock
        self.record = record
        self.on_decided: Callable[[Approval], Awaitable[None]] | None = None  # 보통 resume

    # ---- 엔진 쪽 (ApprovalGate) -------------------------------------------
    async def request(self, run: WorkflowRun, step_id: str, details: dict[str, Any]) -> None:
        aid = approval_id(run.id, step_id)
        now = self.clock()
        async with self.sessionmaker() as s:
            if await s.get(ApprovalRow, aid):
                return  # 멱등: 재개로 다시 들어와도 중복 요청하지 않음
            row = ApprovalRow(
                id=aid,
                run_id=run.id,
                step_id=step_id,
                incident_id=run.state.get("incident_id"),
                status="pending",
                details=details,
                requested_at=now,
                expires_at=now + self.timeout,
            )
            s.add(row)
            await s.commit()
            approval = _to_model(row)
        await self.notifier.approval_requested(
            {**approval.model_dump(mode="json"), "summary": approval.summary}
        )
        await self._timeline(approval, "approval", "orchestrator", f"승인 요청: {approval.summary}")

    async def decision(self, run: WorkflowRun, step_id: str) -> ApprovalDecision | None:
        a = await self.get(approval_id(run.id, step_id))
        if a is None or a.status == "pending":
            return None
        return ApprovalDecision(
            approved=a.status == "approved", actor=a.decided_by or "?", reason=a.reason
        )

    # ---- 사람 쪽 ------------------------------------------------------------
    async def get(self, aid: str) -> Approval | None:
        async with self.sessionmaker() as s:
            row = await s.get(ApprovalRow, aid)
            return _to_model(row) if row else None

    async def find(self, status: str | None = None) -> list[Approval]:
        stmt = select(ApprovalRow).order_by(ApprovalRow.requested_at)
        if status:
            stmt = stmt.where(ApprovalRow.status == status)
        async with self.sessionmaker() as s:
            return [_to_model(r) for r in await s.scalars(stmt)]

    async def decide(
        self, aid: str, approved: bool, actor: str, reason: str = "", *, status: str | None = None
    ) -> Approval:
        async with self.sessionmaker() as s:
            row = await s.get(ApprovalRow, aid)
            if row is None:
                raise KeyError(f"approval not found: {aid}")
            if row.status != "pending":
                raise ApprovalConflict(f"이미 결정됨: {row.status} by {row.decided_by}")
            row.status = status or ("approved" if approved else "rejected")
            row.decided_at, row.decided_by, row.reason = self.clock(), actor, reason
            await s.commit()
            approval = _to_model(row)
        APPROVAL_DECISIONS.labels(approval.status).inc()
        APPROVAL_LATENCY.observe((approval.decided_at - approval.requested_at).total_seconds())
        await self._timeline(
            approval, "approval", actor, f"{approval.status}" + (f": {reason}" if reason else "")
        )
        return approval

    async def after_decision(self, approval: Approval) -> None:
        """결정 후 처리(보통 워크플로우 재개). 오래 걸리므로 API 는 백그라운드로 호출한다."""
        if self.on_decided:
            await self.on_decided(approval)

    async def sweep(self) -> dict[str, list[str]]:
        """주기 실행: 미응답 에스컬레이션 · 만료 자동 거부."""
        now = self.clock()
        escalated, expired = [], []
        for a in await self.find("pending"):
            if now >= a.expires_at:
                decided = await self.decide(
                    a.id,
                    False,
                    "system:timeout",
                    f"{self.timeout} 동안 응답 없음 — 안전을 위해 자동 거부",
                    status="expired",
                )
                await self.after_decision(decided)
                expired.append(a.id)
            elif a.escalated_at is None and now >= a.requested_at + self.escalate_after:
                async with self.sessionmaker() as s:
                    row = await s.get(ApprovalRow, a.id)
                    row.escalated_at = now
                    await s.commit()
                await self.notifier.escalated({**a.model_dump(mode="json"), "summary": a.summary})
                await self._timeline(a, "escalation", "system", "승인 미응답 — 에스컬레이션")
                escalated.append(a.id)
        return {"escalated": escalated, "expired": expired}

    async def _timeline(self, a: Approval, kind: str, actor: str, message: str) -> None:
        if self.record and a.incident_id:
            await self.record(a.incident_id, kind, actor, message, approval_id=a.id)
