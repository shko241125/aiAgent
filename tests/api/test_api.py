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
