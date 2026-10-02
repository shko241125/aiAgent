"""Workflow Engine (4.5) — DAG 기반 비동기 실행 + 체크포인트·재개·승인 대기 (M3-01).

에이전트 워크플로우(1.3)와 운영 자동화(2.3)가 공통으로 사용한다.
- 의존성(depends_on)으로 DAG 구성 → 위상 정렬 레이어 단위로 병렬 실행(asyncio.gather)
- 단계별 재시도·타임아웃·조건부 실행(condition), 실패한 단계의 하위는 SKIPPED
- 체크포인트: 단계 상태가 바뀔 때마다 RunStore 에 저장 (durable execution)
- 승인(approval) 단계: 결정이 없으면 WAITING 으로 일시정지 → 결정 후 resume() 으로 이어서 실행
- 재개 시 끝난 단계(SUCCEEDED/SKIPPED/FAILED)는 건너뛴다. 실행 중(RUNNING)에 죽은 단계는
  다시 실행되므로 단계 동작은 멱등(idempotent)이어야 한다 (at-least-once).

단계 출력은 항상 JSON 형태로 정규화한다
→ 새로 실행한 run 과 DB 에서 재개한 run 이 같은 데이터를 본다.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aiops.domain.models import new_id, utcnow

logger = logging.getLogger(__name__)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"  # 사람 승인 대기
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


DONE_STEP = {StepStatus.SUCCEEDED, StepStatus.SKIPPED, StepStatus.FAILED}


class StepRecord(BaseModel):
    status: StepStatus = StepStatus.PENDING
    output: Any = None
    error: str | None = None
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("wf"))
    workflow: str
    status: RunStatus = RunStatus.RUNNING
    state: dict[str, Any] = Field(default_factory=dict)  # 입력·공유 상태 (JSON 직렬화 가능해야 함)
    steps: dict[str, StepRecord] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def output(self, step_id: str) -> Any:
        return self.steps[step_id].output

    def waiting_steps(self) -> list[str]:
        return [k for k, v in self.steps.items() if v.status == StepStatus.WAITING]


StepAction = Callable[[WorkflowRun], Awaitable[Any]]


class Step(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    action: StepAction | None = None
    kind: Literal["action", "approval"] = "action"
    # approval 단계: 승인 요청 내용(무엇을 왜 하려는지)을 만드는 함수
    describe: Callable[[WorkflowRun], Awaitable[dict[str, Any]]] | None = None
    depends_on: list[str] = Field(default_factory=list)  # 선행이 실패·건너뜀이면 같이 건너뜀
    after: list[str] = Field(default_factory=list)  # 순서만 따름 — 선행 결과와 무관하게 실행
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
        deps = {s.id: list(dict.fromkeys(s.depends_on + s.after)) for s in self.steps}
        for sid, ds in deps.items():
            for d in ds:
                if d not in by_id:
                    raise ValueError(f"step '{sid}' depends on unknown step '{d}'")
        indeg = {sid: len(ds) for sid, ds in deps.items()}
        layers: list[list[Step]] = []
        ready = [sid for sid, n in indeg.items() if n == 0]
        seen = 0
        while ready:
            layers.append([by_id[sid] for sid in ready])
            seen += len(ready)
            nxt = []
            for sid in ready:
                for s in self.steps:
                    if sid in deps[s.id]:
                        indeg[s.id] -= 1
                        if indeg[s.id] == 0:
                            nxt.append(s.id)
            ready = nxt
        if seen != len(self.steps):
            raise ValueError(f"workflow '{self.name}' has a cycle")
        return layers


# ---- 체크포인트 저장소 ---------------------------------------------------------
class RunStore(ABC):
    @abstractmethod
    async def save(self, run: WorkflowRun) -> None: ...

    @abstractmethod
    async def get(self, run_id: str) -> WorkflowRun | None: ...

    @abstractmethod
    async def list(self, status: RunStatus | None = None) -> list[WorkflowRun]: ...


class InMemoryRunStore(RunStore):
    def __init__(self) -> None:
        self._runs: dict[str, str] = {}  # JSON 으로 보관 → DB 저장소와 같은 직렬화 경로를 탄다

    async def save(self, run: WorkflowRun) -> None:
        self._runs[run.id] = run.model_dump_json()

    async def get(self, run_id: str) -> WorkflowRun | None:
        raw = self._runs.get(run_id)
        return WorkflowRun.model_validate_json(raw) if raw else None

    async def list(self, status: RunStatus | None = None) -> list[WorkflowRun]:
        runs = [WorkflowRun.model_validate_json(r) for r in self._runs.values()]
        return [r for r in runs if status is None or r.status == status]


# ---- 승인 관문 -----------------------------------------------------------------
class ApprovalDecision(BaseModel):
    approved: bool
    actor: str
    reason: str = ""


class ApprovalGate(ABC):
    @abstractmethod
    async def decision(self, run: WorkflowRun, step_id: str) -> ApprovalDecision | None:
        """결정이 났으면 반환, 아직이면 None."""

    @abstractmethod
    async def request(self, run: WorkflowRun, step_id: str, details: dict[str, Any]) -> None:
        """승인 요청을 기록·알린다 (첫 대기 진입 시 1회)."""


class InMemoryApprovalGate(ApprovalGate):
    def __init__(self) -> None:
        self.requests: dict[tuple[str, str], dict[str, Any]] = {}
        self.decisions: dict[tuple[str, str], ApprovalDecision] = {}

    async def decision(self, run: WorkflowRun, step_id: str) -> ApprovalDecision | None:
        return self.decisions.get((run.id, step_id))

    async def request(self, run: WorkflowRun, step_id: str, details: dict[str, Any]) -> None:
        self.requests[(run.id, step_id)] = details

    def decide(self, run_id: str, step_id: str, approved: bool, actor: str, reason: str = ""):
        self.decisions[(run_id, step_id)] = ApprovalDecision(
            approved=approved, actor=actor, reason=reason
        )


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    return value


WorkflowFactory = Callable[[WorkflowRun], Awaitable[Workflow]]


class WorkflowEngine:
    def __init__(
        self,
        on_step_update: Callable[[WorkflowRun, str], Awaitable[None]] | None = None,
        store: RunStore | None = None,
        approvals: ApprovalGate | None = None,
    ):
        self.on_step_update = on_step_update  # 진행 상황 알림 훅
        self.store = store
        self.approvals = approvals
        self.factories: dict[str, WorkflowFactory] = {}  # 재개 시 정의 재구성
        # 워크플로우가 끝났을 때(성공·실패) 후처리 — 처음 실행이든 재개든 같은 지점에서 호출
        self.on_complete: dict[str, Callable[[WorkflowRun], Awaitable[None]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, name: str, factory: WorkflowFactory) -> None:
        self.factories[name] = factory

    async def _checkpoint(self, run: WorkflowRun, step_id: str | None = None) -> None:
        run.updated_at = utcnow().isoformat()
        if self.store:
            await self.store.save(run)
        if self.on_step_update and step_id:
            await self.on_step_update(run, step_id)

    async def _run_step(self, step: Step, run: WorkflowRun) -> None:
        rec = run.steps[step.id]
        if rec.status in DONE_STEP:
            return  # 재개: 이미 끝난 단계
        upstream_bad = any(
            run.steps[d].status in (StepStatus.FAILED, StepStatus.SKIPPED) for d in step.depends_on
        )
        if upstream_bad or (step.condition and not step.condition(run)):
            rec.status = StepStatus.SKIPPED
            await self._checkpoint(run, step.id)
            return
        if step.kind == "approval":
            await self._approval(step, run, rec)
            return
        rec.status, rec.started_at = StepStatus.RUNNING, utcnow().isoformat()
        await self._checkpoint(run, step.id)
        for attempt in range(1, step.retries + 2):
            rec.attempts = attempt
            try:
                coro = step.action(run)
                out = await (asyncio.wait_for(coro, step.timeout_s) if step.timeout_s else coro)
                rec.output, rec.status, rec.error = to_jsonable(out), StepStatus.SUCCEEDED, None
                break
            except Exception as exc:  # noqa: BLE001
                rec.error = repr(exc)
                logger.warning("step %s attempt %d failed: %s", step.id, attempt, exc)
        else:
            rec.status = StepStatus.FAILED
        rec.finished_at = utcnow().isoformat()
        await self._checkpoint(run, step.id)

    async def _approval(self, step: Step, run: WorkflowRun, rec: StepRecord) -> None:
        if self.approvals is None:
            rec.status, rec.error = StepStatus.FAILED, "승인 관문(ApprovalGate)이 설정되지 않음"
            await self._checkpoint(run, step.id)
            return
        decision = await self.approvals.decision(run, step.id)
        if decision is None:
            if rec.status != StepStatus.WAITING:  # 첫 진입에만 요청
                details = to_jsonable(await step.describe(run)) if step.describe else {}
                rec.output = {"request": details}
                rec.status, rec.started_at = StepStatus.WAITING, utcnow().isoformat()
                await self._checkpoint(run, step.id)
                await self.approvals.request(run, step.id, details)
            return
        rec.output = {**(rec.output or {}), **decision.model_dump()}
        rec.status, rec.finished_at = StepStatus.SUCCEEDED, utcnow().isoformat()
        await self._checkpoint(run, step.id)

    async def _execute(self, workflow: Workflow, run: WorkflowRun) -> WorkflowRun:
        lock = self._locks.setdefault(run.id, asyncio.Lock())
        async with lock:  # 같은 run 의 동시 재개 방지 (프로세스 내)
            run.status = RunStatus.RUNNING
            await self._checkpoint(run)
            for layer in workflow.layers():
                await asyncio.gather(*(self._run_step(s, run) for s in layer))
                # 현재 레이어만 본다 — 재개 직후엔 뒤 레이어에 '이전 대기 상태'가 남아 있을 수 있다
                if any(run.steps[s.id].status == StepStatus.WAITING for s in layer):
                    run.status = RunStatus.WAITING  # 이후 레이어는 승인 결정 뒤 resume 에서
                    await self._checkpoint(run)
                    return run
            failed = any(r.status == StepStatus.FAILED for r in run.steps.values())
            run.status = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
            await self._checkpoint(run)
        if hook := self.on_complete.get(run.workflow):
            await hook(run)
        return run

    async def run(
        self, workflow: Workflow, state: dict[str, Any] | None = None, run_id: str | None = None
    ) -> WorkflowRun:
        run = WorkflowRun(
            workflow=workflow.name,
            state=to_jsonable(state or {}),
            steps={s.id: StepRecord() for s in workflow.steps},
            **({"id": run_id} if run_id else {}),
        )
        return await self._execute(workflow, run)

    async def resume(self, run_id: str, workflow: Workflow | None = None) -> WorkflowRun:
        """저장된 run 을 이어서 실행. workflow 를 주지 않으면 등록된 팩토리로 재구성한다."""
        if self.store is None:
            raise RuntimeError("재개하려면 RunStore 가 필요합니다")
        run = await self.store.get(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED):
            return run
        if workflow is None:
            if run.workflow not in self.factories:
                raise KeyError(f"재구성할 워크플로우 팩토리가 없음: {run.workflow}")
            workflow = await self.factories[run.workflow](run)
        for s in workflow.steps:  # 정의에 새로 생긴 단계가 있으면 대기 상태로 추가
            run.steps.setdefault(s.id, StepRecord())
        return await self._execute(workflow, run)
