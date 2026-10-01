import pytest
from fastapi.testclient import TestClient

from aiops.llm.base import LLMResponse
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.main import create_app


@pytest.fixture
def llm():
    return FakeLLMProvider()


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings, llm=llm)) as c:
        yield c


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert "detection" in ready["agents"] and ready["knowledge_chunks"] > 0


def test_anomaly_endpoint(client):
    values = [10.0] * 30 + [100.0] + [10.0] * 5
    res = client.post(
        "/api/v1/analytics/anomalies", json={"values": values, "method": "robust_zscore"}
    )
    assert res.status_code == 200
    assert 30 in [a["index"] for a in res.json()]


def test_rag_search_uses_seed_knowledge(client):
    res = client.post("/api/v1/rag/search", json={"query": "HikariPool 커넥션 고갈", "k": 3})
    assert res.status_code == 200
    assert res.json()[0]["doc_id"] in {
        "runbook-db-connection-pool",
        "postmortem-2026-08-order-service",
    }


def test_incident_response_workflow(client, llm):
    llm.push(
        LLMResponse(content='{"is_incident": true, "severity": "major", "summary": "지연"}'),
        LLMResponse(content='{"root_cause": "커넥션 누수", "confidence": 0.7}'),
    )
    res = client.post(
        "/api/v1/orchestrations/incident-response",
        json={"alert": {"service": "order-service", "title": "latency high", "severity": "major"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["workflow_status"] == "succeeded"
    assert all(s["status"] == "succeeded" for s in body["steps"].values())

    inc = client.get(f"/api/v1/incidents/{body['incident_id']}").json()
    assert inc["root_cause"] == "커넥션 누수"


def test_workflow_stops_when_not_incident(client, llm):
    llm.push(LLMResponse(content='{"is_incident": false}'))
    body = client.post(
        "/api/v1/orchestrations/incident-response",
        json={"alert": {"service": "user-service", "title": "blip"}},
    ).json()
    assert body["steps"]["rca"]["status"] == "skipped"
    assert body["steps"]["report"]["status"] == "skipped"


def test_supervisor_routes_then_finishes(client, llm):
    llm.push(
        LLMResponse(content='{"next": "knowledge", "instruction": "커넥션 풀 대응법"}'),
        LLMResponse(content="runbook 에 따르면 ... [runbook-db-connection-pool]"),
        LLMResponse(content='{"next": "FINISH"}'),
    )
    body = client.post("/api/v1/orchestrations/supervised", json={"goal": "커넥션 풀 대응"}).json()
    assert [r["agent"] for r in body["results"]] == ["knowledge"]


def test_resolve_incident_accumulates_knowledge_and_survives_restart(settings, llm):
    """M1-09: 해결된 인시던트가 지식이 되고, 재시작 후에도 검색된다."""
    with TestClient(create_app(settings, llm=llm)) as c:
        inc = c.post(
            "/api/v1/incidents",
            json={
                "title": "정산 배치 지연",
                "service": "settlement-batch",
                "severity": "major",
                "summary": "정산 배치가 3시간 지연",
            },
        ).json()
        res = c.post(
            f"/api/v1/incidents/{inc['id']}/resolve",
            json={
                "resolution": "파티션 프루닝 누락 쿼리를 수정하고 재실행",
                "root_cause": "월말 파티션 프루닝 누락으로 전체 테이블 스캔",
                "actions": ["쿼리 수정", "배치 재실행"],
            },
        ).json()
        assert res["incident"]["status"] == "resolved"
        assert res["ingest"]["added"] == [res["knowledge_doc_id"]]
        hits = c.post("/api/v1/rag/search", json={"query": "파티션 프루닝 누락", "k": 1}).json()
        assert hits[0]["doc_id"] == f"incident-{inc['id']}"

    with TestClient(create_app(settings, llm=llm)) as c:  # 같은 DB 로 재시작
        hits = c.post("/api/v1/rag/search", json={"query": "파티션 프루닝 누락", "k": 1}).json()
        assert hits[0]["doc_id"] == f"incident-{inc['id']}"


def test_rag_answer_reports_citations(client, llm):
    llm.push(LLMResponse(content="커넥션 풀을 확인하세요 [1]. 배포 이력도 보세요."))
    body = client.post("/api/v1/rag/answer", json={"query": "HikariPool 커넥션 고갈"}).json()
    report = body["citation_report"]
    assert report["valid"] and report["citation_rate"] == 0.5


AM_PAYLOAD = {
    "version": "4",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "abc",
            "startsAt": "2026-10-01T01:00:00Z",
            "labels": {
                "alertname": "HighLatency",
                "service": "order-service",
                "severity": "critical",
            },
            "annotations": {"summary": "p95 > 2s"},
        }
    ],
}


def test_alertmanager_webhook_is_idempotent(client):
    first = client.post("/api/v1/events/alertmanager", json=AM_PAYLOAD).json()
    again = client.post("/api/v1/events/alertmanager", json=AM_PAYLOAD).json()
    assert (first["stored"], again["stored"]) == (1, 0)
    assert first["incident_triggered_for"] == []  # 자동 대응은 기본 꺼짐
    events = client.get(
        "/api/v1/events", params={"service": "order-service", "minutes": 10**7}
    ).json()
    assert [e["type"] for e in events] == ["alert.HighLatency"]


def test_alert_auto_triggers_incident_and_suppresses_duplicates(settings, llm):
    s = settings.model_copy(update={"auto_incident_on_alert": True})
    with TestClient(create_app(s, llm=llm)) as c:
        r1 = c.post("/api/v1/events/alertmanager", json=AM_PAYLOAD).json()
        assert r1["incident_triggered_for"] == ["order-service"]
        incidents = c.get("/api/v1/incidents").json()
        assert len(incidents) == 1 and incidents[0]["status"] == "investigating"

        second = {**AM_PAYLOAD, "alerts": [{**AM_PAYLOAD["alerts"][0], "fingerprint": "def"}]}
        r2 = c.post("/api/v1/events/alertmanager", json=second).json()
        assert r2["incident_triggered_for"] == [] and len(r2["suppressed"]) == 1
        assert len(c.get("/api/v1/incidents").json()) == 1  # 알람 폭주 → 인시던트 1건 유지
