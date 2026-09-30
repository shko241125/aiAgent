import pytest

from aiops.workflow.engine import RunStatus, Step, StepStatus, Workflow, WorkflowEngine


def _const(v):
    async def action(run):
        return v

    return action


async def test_dag_runs_in_dependency_order():
    order: list[str] = []

    def rec(name):
        async def action(run):
            order.append(name)
            return name

        return action

    wf = Workflow(
        name="t",
        steps=[
            Step(id="c", action=rec("c"), depends_on=["a", "b"]),
            Step(id="a", action=rec("a")),
            Step(id="b", action=rec("b")),
        ],
    )
    run = await WorkflowEngine().run(wf)
    assert run.status == RunStatus.SUCCEEDED
    assert order[-1] == "c" and set(order[:2]) == {"a", "b"}


async def test_failure_skips_downstream_and_retries():
    attempts = {"n": 0}

    async def flaky(run):
        attempts["n"] += 1
        raise RuntimeError("boom")

    wf = Workflow(
        name="t",
        steps=[
            Step(id="a", action=flaky, retries=2),
            Step(id="b", action=_const(1), depends_on=["a"]),
        ],
    )
    run = await WorkflowEngine().run(wf)
    assert attempts["n"] == 3
    assert run.steps["a"].status == StepStatus.FAILED
    assert run.steps["b"].status == StepStatus.SKIPPED
    assert run.status == RunStatus.FAILED


async def test_condition_skip():
    wf = Workflow(
        name="t",
        steps=[
            Step(id="a", action=_const(False)),
            Step(id="b", action=_const(1), depends_on=["a"], condition=lambda r: r.output("a")),
        ],
    )
    run = await WorkflowEngine().run(wf)
    assert run.steps["b"].status == StepStatus.SKIPPED
    assert run.status == RunStatus.SUCCEEDED


def test_cycle_detection():
    wf = Workflow(
        name="t",
        steps=[
            Step(id="a", action=_const(1), depends_on=["b"]),
            Step(id="b", action=_const(1), depends_on=["a"]),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        wf.layers()
