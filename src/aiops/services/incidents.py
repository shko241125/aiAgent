"""인시던트 상태 머신 + 타임라인 (M3-04 / 2.4).

상태 전이
    OPEN ─▶ INVESTIGATING ─▶ MITIGATING ─▶ RESOLVED ─▶ CLOSED
      │           ▲               │            │
      │           └── 조치 실패 ───┘            └─▶ INVESTIGATING (재발)
      └─────────────▶ RESOLVED (오탐·자연 복구)
CLOSED 는 종결 — 재발은 새 인시던트로 연다 (포스트모템·지표 집계를 깨지 않기 위해).
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiops.db.models import IncidentEventRow, IncidentRow
from aiops.domain.models import Incident, IncidentStatus, new_id, utcnow

S = IncidentStatus
TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    S.OPEN: {S.INVESTIGATING, S.MITIGATING, S.RESOLVED},
    S.INVESTIGATING: {S.MITIGATING, S.RESOLVED},
    S.MITIGATING: {S.INVESTIGATING, S.RESOLVED},
    S.RESOLVED: {S.CLOSED, S.INVESTIGATING},
    S.CLOSED: set(),
}


class InvalidTransition(ValueError):
    pass


class TimelineEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tl"))
    incident_id: str
    ts: datetime = Field(default_factory=utcnow)
    kind: str  # status | alert | detection | rca | plan | approval | action | verification | ...
    actor: str
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class IncidentService:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def get(self, incident_id: str) -> Incident | None:
        async with self.sessionmaker() as s:
            row = await s.get(IncidentRow, incident_id)
            return Incident.model_validate(row, from_attributes=True) if row else None

    async def record(
        self, incident_id: str, kind: str, actor: str, message: str = "", **data: Any
    ) -> TimelineEvent:
        ev = TimelineEvent(
            incident_id=incident_id, kind=kind, actor=actor, message=message, data=data
        )
        async with self.sessionmaker() as s:
            s.add(IncidentEventRow(**ev.model_dump(mode="python")))
            await s.commit()
        return ev

    async def transition(
        self, incident_id: str, to: IncidentStatus, actor: str, note: str = ""
    ) -> Incident:
        async with self.sessionmaker() as s:
            row = await s.get(IncidentRow, incident_id)
            if row is None:
                raise KeyError(f"incident not found: {incident_id}")
            frm = IncidentStatus(row.status)
            if to == frm:
                return Incident.model_validate(row, from_attributes=True)
            if to not in TRANSITIONS[frm]:
                allowed = ", ".join(sorted(TRANSITIONS[frm])) or "(없음 — 종결 상태)"
                raise InvalidTransition(f"{frm} → {to} 전이 불가 (허용: {allowed})")
            row.status, row.updated_at = to.value, utcnow()
            await s.commit()
            inc = Incident.model_validate(row, from_attributes=True)
        await self.record(
            incident_id,
            "status",
            actor,
            f"{frm} → {to}" + (f": {note}" if note else ""),
            frm=frm.value,
            to=to.value,
        )
        return inc

    async def timeline(self, incident_id: str) -> list[TimelineEvent]:
        async with self.sessionmaker() as s:
            rows = await s.scalars(
                select(IncidentEventRow)
                .where(IncidentEventRow.incident_id == incident_id)
                .order_by(IncidentEventRow.ts)
            )
            return [
                TimelineEvent.model_validate(
                    {
                        **{
                            c: getattr(r, c)
                            for c in ("id", "incident_id", "kind", "actor", "message", "data")
                        },
                        "ts": r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=UTC),
                    }
                )
                for r in rows
            ]

    @staticmethod
    def mttr_minutes(timeline: list[TimelineEvent]) -> float | None:
        """첫 이벤트 → RESOLVED 전이까지 (분). 운영 핵심 지표(MTTR)."""
        resolved = next(
            (
                e
                for e in timeline
                if e.kind == "status" and e.data.get("to") == IncidentStatus.RESOLVED.value
            ),
            None,
        )
        if not timeline or resolved is None:
            return None
        start: datetime = timeline[0].ts
        return round((resolved.ts - start).total_seconds() / 60, 2)
