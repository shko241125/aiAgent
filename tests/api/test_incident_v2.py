"""M3-06 인시던트 대응 v2 E2E — 계획 → 승인 → 실행 → 효과 검증 (+ 재시작 후 재개)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiops.evals.rca import load_scenarios
from aiops.llm.base import LLMResponse
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.main import create_app

SCENARIOS = {
    s.id: s
    for s in load_scenarios(Path(__file__).resolve().parents[2] / "data/eval/rca_scenarios.json")
}
ALERT = {"alert": {"service": "order-service", "title": "5xx 급증", "severity": "major"}}


@pytest.fixture
def s(settings):
    return settings.model_copy(
        update={
            "remediation_verify_interval_s": 0,
            "remediation_cooldown_s": 0,
            "approval_sweep_interval_s": 0,
        }
    )


def scripted_llm():
    return FakeLLMProvider(
        [
            LLMResponse(content='{"is_incident": true, "severity": "major", "summary": "5xx"}'),
            LLMResponse(
                content='{"root_cause": "v2.3.1 배포 후 커넥션 풀 고갈", "confidence": 0.8,'
                ' "service": "order-service"}'
            ),
        ]
    )


def start(c, scenario="deploy-pool", fixed_by=None):
    sc = SCENARIOS[scenario].model_copy(deep=True)
    if fixed_by is not None:
        for f in sc.faults:
            f.fixed_by = fixed_by
    c.app.state.platform.source.apply_scenario(sc)
    body = c.post("/api/v1/orchestrations/incident-response", json=ALERT).json()
    return sc, body


def board(c, body):
    cols = c.get(f"/api/v1/boards/{body['board_id']}").json()["columns"]
    return {card["id"]: col for col, cards in cols.items() for card in cards}


def test_approve_execute_verify_resolve(s):
    with TestClient(create_app(s, llm=scripted_llm())) as c:
        _, body = start(c)
        assert body["workflow_status"] == "waiting"
        apr = body["approval"]
        assert apr["details"]["actions"][0]["type"] == "rollback"
        assert "dry-run" in apr["details"]["dry_run"]  # 승인 화면에 실행 가능성 표시
        cards = body["cards"]
        assert board(c, body)[cards["approve"]] == "blocked"  # 사람 승인 대기 = BLOCKED
        iid = body["incident_id"]
        assert c.get(f"/api/v1/incidents/{iid}").json()["status"] == "mitigating"

        c.post(f"/api/v1/approvals/{apr['id']}/approve", json={"actor": "kim", "reason": "확인"})

        inc = c.get(f"/api/v1/incidents/{iid}").json()
        assert inc["status"] == "resolved"
        tl = [e["kind"] for e in c.get(f"/api/v1/incidents/{iid}/timeline").json()["events"]]
        for kind in ("alert", "detection", "rca", "plan", "approval", "action", "verification"):
            assert kind in tl
        b = board(c, body)
        assert b[cards["approve"]] == b[cards["execute"]] == b[cards["report"]] == "done"
        assert b[cards["epic"]] == "done"


def test_resume_after_restart_while_waiting(s):
    with TestClient(create_app(s, llm=scripted_llm())) as c:
        sc, body = start(c)
    # ── 프로세스 재시작: 메모리(Blackboard·시뮬레이터)는 사라지고 DB 만 남는다 ──
    with TestClient(create_app(s, llm=FakeLLMProvider())) as c2:
        c2.app.state.platform.source.apply_scenario(sc)  # 바깥 세상(장애)은 그대로
        pending = c2.get("/api/v1/approvals").json()
        assert [a["id"] for a in pending] == [body["approval"]["id"]]
        c2.post(f"/api/v1/approvals/{pending[0]['id']}/approve", json={"actor": "lee"})
        inc = c2.get(f"/api/v1/incidents/{body['incident_id']}").json()
        assert inc["status"] == "resolved"
        # 재개 후 실행된 incident 에이전트가 복원된 기억(RCA·조치 결과)을 프롬프트로 받았다
        llm = c2.app.state.platform.llm
        prompt = next(
            m.content for call in llm.calls for m in call if m.content and "[RCA]" in m.content
        )
        assert "커넥션 풀 고갈" in prompt and "rollback" in prompt


def test_reject_skips_execution(s):
    with TestClient(create_app(s, llm=scripted_llm())) as c:
        _, body = start(c)
        c.post(
            f"/api/v1/approvals/{body['approval']['id']}/reject",
            json={"actor": "kim", "reason": "피크 시간"},
        )
        iid = body["incident_id"]
        assert c.get(f"/api/v1/incidents/{iid}").json()["status"] == "investigating"
        tl = [e["kind"] for e in c.get(f"/api/v1/incidents/{iid}/timeline").json()["events"]]
        assert "action" not in tl
        b = board(c, body)
        assert b[body["cards"]["approve"]] == "done" and b[body["cards"]["report"]] == "done"


def test_wrong_action_not_recovered_escalates(s):
    with TestClient(create_app(s, llm=scripted_llm())) as c:
        _, body = start(c, fixed_by=["restart"])  # 실제로는 재시작으로만 고쳐지는 장애
        c.post(f"/api/v1/approvals/{body['approval']['id']}/approve", json={"actor": "kim"})
        iid = body["incident_id"]
        assert c.get(f"/api/v1/incidents/{iid}").json()["status"] == "investigating"
        tl = c.get(f"/api/v1/incidents/{iid}/timeline").json()["events"]
        assert any(e["kind"] == "escalation" for e in tl)
        assert any(e["kind"] == "verification" and e["data"]["recovered"] is False for e in tl)
        b = board(c, body)
        assert b[body["cards"]["execute"]] == "blocked" and b[body["cards"]["epic"]] == "blocked"
