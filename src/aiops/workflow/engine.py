"""Workflow Engine (4.5) — DAG 기반 비동기 실행 엔진.

에이전트 워크플로우(1.3)와 운영 자동화 런북(2.3)이 공통으로 사용한다.
- 의존성(depends_on)으로 DAG 구성 → 위상 정렬 레이어 단위로 병렬 실행(asyncio.gather)
- 단계별 재시도·타임아웃·조건부 실행(condition)
- 실패한 단계의 하위 단계는 SKIPPED 처리

TODO(4.5): 실행 상태 DB 영속화 → 프로세스 재시작 후 재개(resume), 사람 승인 대기(wait) 단계,
           장기 실행 워크플로우는 Temporal/Celery 등 외부 엔진 도입 검토.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aiops.domain.models import new_id, utcnow

logger = logging.getLogger(__name__)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepRecord(BaseModel):
    status: StepStatus = StepStatus.PENDING
    output: Any = None
    error: str | None = None
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None


class WorkflowRun(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: new_id("wf"))
    workflow: str
    status: RunStatus = RunStatus.RUNNING
    state: dict[str, Any] = Field(default_factory=dict)  # 워크플로우 입력·공유 상태
    steps: dict[str, StepRecord] = Field(default_factory=dict)

    def output(self, step_id: str) -> Any:
        return self.steps[step_id].output


StepAction = Callable[[WorkflowRun], Awaitable[Any]]


class Step(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    action: StepAction
    depends_on: list[str] = Field(default_factory=list)
    retries: int = 0
    timeout_s: float | None = None
    condition: Callable[[WorkflowRun], bool] | None = None  # False 면 SKIPPED
    description: str = ""


class Workflow(BaseModel):
    name: str
    steps: list[Step]
    description: str = ""

    def layers(self) -> list[list[Step]]:
        """Kahn 알고리즘으로 위상 정렬 → 동시에 실행 가능한 단계 묶음(layer) 목록."""
        by_id = {s.id: s for s in self.steps}
        for s in self.steps:
            for d in s.depends_on:
                if d not in by_id:
                    raise ValueError(f"step '{s.id}' depends on unknown step '{d}'")
        indeg = {s.id: len(s.depends_on) for s in self.steps}
        layers: list[list[Step]] = []
        ready = [sid for sid, n in indeg.items() if n == 0]
        seen = 0
        while ready:
            layers.append([by_id[sid] for sid in ready])
            seen += len(ready)
            nxt = []
            for sid in ready:
                for s in self.steps:
                    if sid in s.depends_on:
                        indeg[s.id] -= 1
                        if indeg[s.id] == 0:
                            nxt.append(s.id)
            ready = nxt
        if seen != len(self.steps):
            raise ValueError(f"workflow '{self.name}' has a cycle")
        return layers


class WorkflowEngine:
    def __init__(self, on_step_update: Callable[[WorkflowRun, str], Awaitable[None]] | None = None):
        self.on_step_update = on_step_update  # 진행 상황 알림/영속화 훅

    async def _notify(self, run: WorkflowRun, step_id: str) -> None:
        if self.on_step_update:
            await self.on_step_update(run, step_id)

    async def _run_step(self, step: Step, run: WorkflowRun) -> None:
        rec = run.steps[step.id]
        upstream_failed = any(
            run.steps[d].status in (StepStatus.FAILED, StepStatus.SKIPPED) for d in step.depends_on
        )
        if upstream_failed or (step.condition and not step.condition(run)):
            rec.status = StepStatus.SKIPPED
            await self._notify(run, step.id)
            return
        rec.status, rec.started_at = StepStatus.RUNNING, utcnow().isoformat()
        await self._notify(run, step.id)
        for attempt in range(1, step.retries + 2):
            rec.attempts = attempt
            try:
                coro = step.action(run)
                rec.output = await (
                    asyncio.wait_for(coro, step.timeout_s) if step.timeout_s else coro
                )
                rec.status, rec.error = StepStatus.SUCCEEDED, None
                break
            except Exception as exc:  # noqa: BLE001
                rec.error = repr(exc)
                logger.warning("step %s attempt %d failed: %s", step.id, attempt, exc)
        else:
            rec.status = StepStatus.FAILED
        rec.finished_at = utcnow().isoformat()
        await self._notify(run, step.id)

    async def run(self, workflow: Workflow, state: dict[str, Any] | None = None) -> WorkflowRun:
        run = WorkflowRun(
            workflow=workflow.name,
            state=state or {},
            steps={s.id: StepRecord() for s in workflow.steps},
        )
        for layer in workflow.layers():
            await asyncio.gather(*(self._run_step(s, run) for s in layer))
        failed = any(r.status == StepStatus.FAILED for r in run.steps.values())
        run.status = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
        return run
