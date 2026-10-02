"""M4-03 관측성 — /metrics(라우트 템플릿 라벨), LLM·워크플로우 계측, /ready 실점검, SLO 생성물."""

import importlib.util
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from tests.api.test_incident_v2 import ALERT, SCENARIOS, scripted_llm

from aiops.llm.router import LLMRouter
from aiops.main import create_app
from aiops.observability.metrics import LATENCY_BUCKETS, REGISTRY
from aiops.observability.slo import LATENCY_THRESHOLD_S, SLOS, prometheus_rules

ROOT = Path(__file__).resolve().parents[2]


def val(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_use_route_templates(settings):
    with TestClient(create_app(settings)) as c:
        before = val(
            "aiops_http_requests_total",
            method="GET",
            route="/api/v1/incidents/{incident_id}",
            **{"class": "interactive"},
            code="404",
        )
        assert c.get("/api/v1/incidents/inc-nope").status_code == 404
        c.get("/no/such/path")
        body = c.get("/metrics").text
        assert 'route="/api/v1/incidents/{incident_id}"' in body
        assert "inc-nope" not in body  # 실제 URL 값은 라벨에 들어가지 않는다
        assert 'route="unmatched"' in body
        assert 'route="/metrics"' not in body
        after = val(
            "aiops_http_requests_total",
            method="GET",
            route="/api/v1/incidents/{incident_id}",
            **{"class": "interactive"},
            code="404",
        )
        assert after == before + 1
        assert "process_cpu_seconds_total" in body or "python_gc" in body


def test_llm_workflow_approval_metrics(settings):
    s = settings.model_copy(
        update={
            "remediation_verify_interval_s": 0,
            "remediation_cooldown_s": 0,
            "approval_sweep_interval_s": 0,
        }
    )
    llm_before = val("aiops_llm_requests_total", provider="fake", agent="detection", outcome="ok")
    step_before = val(
        "aiops_workflow_steps_total",
        workflow="incident_response_v2",
        step="rca",
        status="succeeded",
    )
    real_before = val("aiops_remediation_actions_total", type="rollback", mode="real", outcome="ok")
    router = LLMRouter({"fake": scripted_llm()}, default="fake")  # 운영처럼 라우터 경유
    with TestClient(create_app(s, llm=router)) as c:
        c.app.state.platform.source.apply_scenario(SCENARIOS["deploy-pool"].model_copy(deep=True))
        body = c.post("/api/v1/orchestrations/incident-response", json=ALERT).json()
        c.post(f"/api/v1/approvals/{body['approval']['id']}/approve", json={"actor": "kim"})
    assert val("aiops_llm_requests_total", provider="fake", agent="detection", outcome="ok") > (
        llm_before
    )
    assert (
        val(
            "aiops_workflow_steps_total",
            workflow="incident_response_v2",
            step="rca",
            status="succeeded",
        )
        == step_before + 1
    )
    assert val("aiops_approval_decisions_total", status="approved") >= 1
    assert (
        val("aiops_remediation_actions_total", type="rollback", mode="real", outcome="ok")
        == real_before + 1
    )


def test_ready_checks_database_and_reports_degraded_llm(settings):
    with TestClient(create_app(settings)) as c:
        r = c.get("/ready")
        assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
        p = c.app.state.platform
        breaker = p.llm.breakers["fake"]
        breaker._opened_at = time.monotonic()  # 서킷 열림 → 동작은 하지만 degraded
        r = c.get("/ready")
        assert r.status_code == 200 and r.json()["status"] == "degraded"
        breaker._opened_at = None
        good = p.sessionmaker

        def broken():
            raise ConnectionError("db down")

        p.sessionmaker = broken
        r = c.get("/ready")
        assert r.status_code == 503 and r.json()["status"] == "not_ready"
        p.sessionmaker = good


def test_slo_rules_are_consistent():
    assert LATENCY_THRESHOLD_S in LATENCY_BUCKETS
    rules = [r for g in prometheus_rules()["groups"] for r in g["rules"]]
    records = {r["record"] for r in rules if "record" in r}
    alerts = [r for r in rules if "alert" in r]
    assert len(alerts) == 4 * len(SLOS)
    for a in alerts:  # 알람은 정의된 기록 규칙만 참조한다
        refs = [t for t in a["expr"].split() if t.startswith("slo:")]
        assert refs and set(refs) <= records


def test_generated_observability_files_are_fresh():
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts/gen_observability.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for path, content in gen.render().items():
        assert path.read_text(encoding="utf-8") == content, (
            f"{path} 가 SLO 정의와 다릅니다 → python scripts/gen_observability.py"
        )
    yaml.safe_load((ROOT / "deploy/prometheus/aiops-slo-rules.yml").read_text())
