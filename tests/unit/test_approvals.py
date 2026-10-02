"""M3-05 사람 승인 — 엔진 연동 · 에스컬레이션 · 만료 · Slack."""

import hashlib
import hmac
import json
import time
from datetime import timedelta

import httpx
import pytest

from aiops.db.models import create_engine, create_sessionmaker, init_db
from aiops.domain.models import utcnow
from aiops.services.approvals import ApprovalConflict, ApprovalService
from aiops.services.notify import (
    LogNotifier,
    SlackSignatureError,
    SlackWebhookNotifier,
    verify_slack_signature,
)
from aiops.workflow.engine import (
    InMemoryRunStore,
    RunStatus,
    Step,
    StepStatus,
    Workflow,
    WorkflowEngine,
)


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now


@pytest.fixture
async def env(tmp_path):
    db = create_engine(f"sqlite+aiosqlite:///{tmp_path}/a.db")
    await init_db(db)
    clock, notifier, timeline = Clock(), LogNotifier(), []

    async def record(incident_id, kind, actor, message, **data):
        timeline.append((incident_id, kind, actor, message))

    svc = ApprovalService(
        create_sessionmaker(db),
        notifier,
        clock=clock,
        record=record,
        escalate_after=timedelta(minutes=15),
        timeout=timedelta(minutes=60),
    )
    engine = WorkflowEngine(store=InMemoryRunStore(), approvals=svc)
    executed = []

    async def describe(run):
        return {
            "summary": "order-service 재시작",
            "actions": [{"type": "restart", "service": "order-service"}],
        }

    async def execute(run):
        executed.append(run.output("approve"))
        return "done"

    wf = Workflow(
        name="w",
        steps=[
            Step(id="approve", kind="approval", describe=describe),
            Step(
                id="execute",
                action=execute,
                depends_on=["approve"],
                condition=lambda r: r.output("approve")["approved"],
            ),
        ],
    )
    svc.on_decided = lambda a: engine.resume(a.run_id, wf)
    yield svc, engine, wf, clock, notifier, timeline, executed
    await db.dispose()


async def test_approval_roundtrip_resumes_workflow(env):
    svc, engine, wf, clock, notifier, timeline, executed = env
    run = await engine.run(wf, state={"incident_id": "inc-1"})
    [pending] = await svc.find("pending")
    assert pending.summary == "order-service 재시작" and notifier.sent == [
        ("requested", pending.id)
    ]
    await engine.resume(run.id, wf)  # 결정 전 재개 → 중복 요청 없음
    assert len(await svc.find()) == 1

    decided = await svc.decide(pending.id, True, "kim", "확인함")
    await svc.after_decision(decided)
    done = await engine.store.get(run.id)
    assert done.status == RunStatus.SUCCEEDED and executed[0]["actor"] == "kim"
    assert [t[1] for t in timeline] == ["approval", "approval"]
    with pytest.raises(ApprovalConflict):
        await svc.decide(pending.id, False, "lee")


async def test_escalation_then_expiry_rejects_safely(env):
    svc, engine, wf, clock, notifier, timeline, executed = env
    run = await engine.run(wf, state={"incident_id": "inc-2"})
    clock.now += timedelta(minutes=16)
    assert len((await svc.sweep())["escalated"]) == 1
    assert (await svc.sweep())["escalated"] == []  # 에스컬레이션은 한 번만
    clock.now += timedelta(minutes=50)
    assert len((await svc.sweep())["expired"]) == 1
    done = await engine.store.get(run.id)
    assert done.steps["execute"].status == StepStatus.SKIPPED and executed == []
    assert done.output("approve")["actor"] == "system:timeout"
    assert ("escalated", f"apr-{run.id}-approve") in notifier.sent


def _sign(secret, body: bytes, ts: int) -> str:
    return (
        "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    )


def test_slack_signature_verification():
    body, ts = b"payload=%7B%7D", int(time.time())
    verify_slack_signature("s3cret", str(ts), body, _sign("s3cret", body, ts))
    with pytest.raises(SlackSignatureError, match="불일치"):
        verify_slack_signature("s3cret", str(ts), body, _sign("other", body, ts))
    with pytest.raises(SlackSignatureError, match="재생"):
        verify_slack_signature("s3cret", str(ts - 600), body, _sign("s3cret", body, ts - 600))


async def test_slack_notifier_sends_buttons_with_approval_id():
    seen = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: seen.append(json.loads(r.content)) or httpx.Response(200, text="ok")
        )
    )
    n = SlackWebhookNotifier("https://hooks.slack.test/x", http_client=client)
    await n.approval_requested(
        {
            "id": "apr-1",
            "summary": "재시작",
            "details": {"actions": [{"type": "restart", "service": "a"}]},
        }
    )
    buttons = seen[0]["blocks"][1]["elements"]
    assert [b["action_id"] for b in buttons] == ["approve", "reject"]
    assert {b["value"] for b in buttons} == {"apr-1"}
