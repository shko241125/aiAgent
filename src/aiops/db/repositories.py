from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiops.agents.context import AgentContext
from aiops.db.models import AgentRunRow, IncidentRow
from aiops.domain.models import Incident, IncidentStatus


class IncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_domain(row: IncidentRow) -> Incident:
        return Incident.model_validate(row, from_attributes=True)

    async def create(self, incident: Incident) -> Incident:
        self.session.add(IncidentRow(**incident.model_dump()))
        await self.session.commit()
        return incident

    async def get(self, incident_id: str) -> Incident | None:
        row = await self.session.get(IncidentRow, incident_id)
        return self._to_domain(row) if row else None

    async def list(self, status: IncidentStatus | None = None, limit: int = 50) -> list[Incident]:
        stmt = select(IncidentRow).order_by(IncidentRow.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(IncidentRow.status == status.value)
        rows = (await self.session.scalars(stmt)).all()
        return [self._to_domain(r) for r in rows]

    async def update(self, incident_id: str, **fields) -> Incident | None:
        row = await self.session.get(IncidentRow, incident_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        await self.session.commit()
        return self._to_domain(row)


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, ctx: AgentContext, kind: str, status: str, result: dict) -> None:
        self.session.add(
            AgentRunRow(
                id=ctx.run_id,
                incident_id=ctx.incident_id,
                kind=kind,
                status=status,
                result=result,
                trace=[t.model_dump() for t in ctx.trace],
            )
        )
        await self.session.commit()
