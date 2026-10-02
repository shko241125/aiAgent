"""워크플로우 실행 상태 DB 저장소 (M3-01)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiops.db.models import WorkflowRunRow
from aiops.workflow.engine import RunStatus, RunStore, WorkflowRun


class SqlRunStore(RunStore):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def save(self, run: WorkflowRun) -> None:
        data = run.model_dump(mode="json")
        async with self.sessionmaker() as s:
            row = await s.get(WorkflowRunRow, run.id)
            if row is None:
                s.add(
                    WorkflowRunRow(
                        id=run.id, workflow=run.workflow, status=run.status.value, data=data
                    )
                )
            else:
                row.status, row.data = run.status.value, data
            await s.commit()

    async def get(self, run_id: str) -> WorkflowRun | None:
        async with self.sessionmaker() as s:
            row = await s.get(WorkflowRunRow, run_id)
            return WorkflowRun.model_validate(row.data) if row else None

    async def list(self, status: RunStatus | None = None) -> list[WorkflowRun]:
        stmt = select(WorkflowRunRow)
        if status:
            stmt = stmt.where(WorkflowRunRow.status == status.value)
        async with self.sessionmaker() as s:
            return [WorkflowRun.model_validate(r.data) for r in await s.scalars(stmt)]
