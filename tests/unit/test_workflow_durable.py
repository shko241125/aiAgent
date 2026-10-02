"""M3-01 체크포인트 · 승인 대기 · 재시작 후 재개."""

from aiops.db.models import create_engine, create_sessionmaker, init_db
from aiops.workflow.engine import (
    InMemoryApprovalGate,
    InMemoryRunStore,
    RunStatus,
    Step,
    StepStatus,
    Workflow,
    WorkflowEngine,
)
from aiops.workflow.store import SqlRunStore


def make_workflow(log: list[str]) -> Workflow:
    def act(name):
        async def action(run):
            log.append(name)
            return {"did": name}

        return action

    async def describe(run):
        return {"action": "restart order-service", "plan": run.output("plan")}

    return Workflow(
        name="remediate",
        steps=[
            Step(id="plan", action=act("plan")),
            Step(id="approve", kind="approval", describe=describe, depends_on=["plan"]),
            Step(
                id="execute",
                action=act("execute"),
                depends_on=["approve"],
                condition=lambda r: r.output("approve")["approved"],
            ),
            Step(id="report", action=act("report"), depends_on=["execute"]),
        ],
    )


async def test_pauses_for_approval_then_resumes():
    log, gate, store = [], InMemoryApprovalGate(), InMemoryRunStore()
    engine = WorkflowEngine(store=store, approvals=gate)
    wf = make_workflow(log)
    run = await engine.run(wf)
    assert run.status == RunStatus.WAITING and run.waiting_steps() == ["approve"]
    assert log == ["plan"]
    assert gate.requests[(run.id, "approve")]["plan"] == {"did": "plan"}

    again = await engine.resume(run.id, wf)  # 결정 전 재개 → 여전히 대기, 요청 중복 없음
    assert again.status == RunStatus.WAITING and log == ["plan"]

    gate.decide(run.id, "approve", approved=True, actor="oncall", reason="확인")
    done = await engine.resume(run.id, wf)
    assert done.status == RunStatus.SUCCEEDED
    assert log == ["plan", "execute", "report"]  # plan 은 다시 실행되지 않는다
    assert done.output("approve")["actor"] == "oncall"


async def test_rejection_skips_execution():
    log, gate = [], InMemoryApprovalGate()
    engine = WorkflowEngine(store=InMemoryRunStore(), approvals=gate)
    wf = make_workflow(log)
    run = await engine.run(wf)
    gate.decide(run.id, "approve", approved=False, actor="oncall", reason="영향 큼")
    done = await engine.resume(run.id, wf)
    assert done.steps["execute"].status == StepStatus.SKIPPED and log == ["plan"]


async def test_resume_after_process_restart_with_sql_store(tmp_path):
    """프로세스가 죽었다 살아나도(새 엔진·DB 재연결) 등록된 팩토리로 정의를 재구성해 이어간다."""
    url = f"sqlite+aiosqlite:///{tmp_path}/wf.db"
    db = create_engine(url)
    await init_db(db)
    gate, log = InMemoryApprovalGate(), []
    first = WorkflowEngine(store=SqlRunStore(create_sessionmaker(db)), approvals=gate)
    run = await first.run(make_workflow(log))
    await db.dispose()  # ── 재시작 ──

    db2 = create_engine(url)
    second = WorkflowEngine(store=SqlRunStore(create_sessionmaker(db2)), approvals=gate)

    async def factory(r):
        return make_workflow(log)

    second.register("remediate", factory)
    assert [r.id for r in await second.store.list(RunStatus.WAITING)] == [run.id]
    gate.decide(run.id, "approve", approved=True, actor="sre")
    done = await second.resume(run.id)
    assert done.status == RunStatus.SUCCEEDED and log == ["plan", "execute", "report"]
    await db2.dispose()


async def test_step_interrupted_while_running_is_rerun():
    log, store = [], InMemoryRunStore()
    engine = WorkflowEngine(store=store)
    wf = Workflow(name="w", steps=[Step(id="a", action=make_workflow(log).steps[0].action)])
    run = await engine.run(wf)
    run.status, run.steps["a"].status = RunStatus.RUNNING, StepStatus.RUNNING  # 실행 중 사망 흉내
    await store.save(run)
    await engine.resume(run.id, wf)
    assert log == ["plan", "plan"]  # at-least-once → 단계는 멱등이어야 한다


async def test_approval_without_gate_fails_safely():
    wf = Workflow(name="w", steps=[Step(id="approve", kind="approval")])
    run = await WorkflowEngine().run(wf)
    assert run.status == RunStatus.FAILED and "ApprovalGate" in run.steps["approve"].error
