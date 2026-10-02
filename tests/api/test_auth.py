"""M4-01 API 인증·인가 — 401/403, 인증 주체가 곧 행위자, prod fail-closed, Slack 허용 목록."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from tests.api.test_incident_v2 import ALERT, SCENARIOS, scripted_llm

from aiops.api.auth import InsecureConfig, hash_key
from aiops.main import create_app
from aiops.workflow.engine import WorkflowRun

KEYS = {"viewer": "k-view", "operator": "k-op", "approver": "k-apr", "admin": "k-admin"}


def H(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {KEYS[role]}"}


@pytest.fixture
def s(settings):
    keys = [
        {"name": f"{role}-user", "sha256": hash_key(k), "roles": [role]} for role, k in KEYS.items()
    ]
    return settings.model_copy(
        update={
            "auth_mode": "api_key",
            "api_keys": json.dumps(keys),
            "remediation_verify_interval_s": 0,
            "remediation_cooldown_s": 0,
            "approval_sweep_interval_s": 0,
            "slack_signing_secret": "sig",
            "slack_allowed_approvers": "U-KIM",
        }
    )


def test_unauthenticated_401_and_role_403(s):
    with TestClient(create_app(s)) as c:
        assert c.get("/health").status_code == 200  # 공개
        assert c.get("/ready").status_code == 200
        r = c.get("/api/v1/incidents")
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert (
            c.get("/api/v1/incidents", headers={"Authorization": "Bearer nope"}).status_code == 401
        )
        assert c.get("/api/v1/incidents", headers=H("viewer")).status_code == 200
        assert c.get("/api/v1/incidents", headers={"X-API-Key": "k-view"}).status_code == 200
        # 계산만 하는 POST 는 viewer 로 충분
        q = {"query": "커넥션 풀", "k": 1}
        assert c.post("/api/v1/rag/search", json=q, headers=H("viewer")).status_code == 200
        # 상태를 바꾸는 POST 는 operator 이상
        note = {"title": "x", "description": "y"}
        r = c.post("/api/v1/boards/b1/cards", json=note, headers=H("viewer"))
        assert r.status_code == 403 and "operator" in r.json()["detail"]
        assert (
            c.post("/api/v1/boards/b1/cards", json=note, headers=H("operator")).status_code == 201
        )
        assert c.post("/api/v1/approvals/sweep", headers=H("operator")).status_code == 403
        assert c.post("/api/v1/approvals/sweep", headers=H("admin")).status_code == 200
        # 도구 사전 승인은 승인 행위 → operator 불가
        run = {"instruction": "x", "approved_tools": ["restart_service"]}
        r = c.post("/api/v1/agents/detection/run", json=run, headers=H("operator"))
        assert r.status_code == 403


def test_approver_identity_comes_from_key_not_body(s):
    with TestClient(create_app(s, llm=scripted_llm())) as c:
        c.app.state.platform.source.apply_scenario(SCENARIOS["deploy-pool"].model_copy(deep=True))
        body = c.post(
            "/api/v1/orchestrations/incident-response", json=ALERT, headers=H("operator")
        ).json()
        aid = body["approval"]["id"]
        url = f"/api/v1/approvals/{aid}/approve"
        assert c.post(url, json={"actor": "ceo"}, headers=H("operator")).status_code == 403
        r = c.post(url, json={"actor": "ceo", "reason": "ok"}, headers=H("approver"))
        assert r.status_code == 200 and r.json()["decided_by"] == "approver-user"  # 자기 신고 무시
        iid = body["incident_id"]
        assert c.get(f"/api/v1/incidents/{iid}", headers=H("viewer")).json()["status"] == "resolved"
        events = c.get(f"/api/v1/incidents/{iid}/timeline", headers=H("viewer")).json()["events"]
        assert any(e["kind"] == "approval" and e["actor"] == "approver-user" for e in events)


def _slack(c, aid: str, user: dict):
    payload = {"user": user, "actions": [{"action_id": "approve", "value": aid}]}
    body = urlencode({"payload": json.dumps(payload)}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"sig", f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return c.post("/api/v1/approvals/slack/actions", content=body, headers=headers)


def test_slack_needs_allowlisted_user(s):
    with TestClient(create_app(s)) as c:
        run = WorkflowRun(workflow="none", state={})
        c.portal.call(c.app.state.platform.approvals.request, run, "approve", {"summary": "x"})
        aid = f"apr-{run.id}-approve"
        r = _slack(c, aid, {"id": "U-LEE", "username": "lee"})  # 서명은 맞지만 허용 목록 밖
        assert r.status_code == 200 and "권한" in r.json()["text"]
        assert c.get(f"/api/v1/approvals/{aid}", headers=H("viewer")).json()["status"] == "pending"
        r = _slack(c, aid, {"id": "U-KIM", "username": "kim"})
        assert r.json()["text"] == "approved by slack:kim"


def test_prod_refuses_to_start_without_auth(settings):
    with pytest.raises(InsecureConfig):
        create_app(settings.model_copy(update={"env": "prod"}))
    with pytest.raises(InsecureConfig):
        create_app(settings.model_copy(update={"env": "prod", "auth_mode": "api_key"}))
    with pytest.raises(InsecureConfig):  # 켜 놓고 키를 안 주면 dev 에서도 거부
        create_app(settings.model_copy(update={"auth_mode": "api_key"}))


def test_auth_off_keeps_self_reported_actor(settings):
    s = settings.model_copy(update={"approval_sweep_interval_s": 0})
    with TestClient(create_app(s)) as c:
        run = WorkflowRun(workflow="none", state={})
        c.portal.call(c.app.state.platform.approvals.request, run, "approve", {"summary": "x"})
        r = c.post(f"/api/v1/approvals/apr-{run.id}-approve/approve", json={"actor": "kim"})
        assert r.json()["decided_by"] == "kim"
