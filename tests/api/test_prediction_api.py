"""M4-06 예측 API — 서비스 예측(시뮬레이터 점진 악화), 선제 알람 스캔(중복 억제), 용량."""

from fastapi.testclient import TestClient

from aiops.integrations.simulated import Fault, FaultScenario
from aiops.main import create_app


def test_predictive_scan_on_degrading_service(settings):
    with TestClient(create_app(settings)) as c:
        src = c.app.state.platform.source
        src.apply_scenario(
            FaultScenario(
                id="leak",
                alert_service="order-service",
                faults=[
                    Fault(
                        service="order-service",
                        metric="memory_usage",
                        magnitude=1.6,
                        onset_min_ago=40,
                        shape="ramp",
                    )
                ],
            )
        )
        preds = {
            x["metric"]: x["prediction"]
            for x in c.get("/api/v1/analytics/predict/order-service").json()
        }
        assert preds["memory_usage"]["probability"] >= 0.6
        assert preds["cpu_usage"]["probability"] < 0.2
        events = c.post("/api/v1/analytics/predict/scan").json()
        assert [(e["service"], e["attributes"]["metric"]) for e in events] == [
            ("order-service", "memory_usage")
        ]
        assert c.post("/api/v1/analytics/predict/scan").json() == []  # dedup 창 안 재알람 없음
        stored = c.get("/api/v1/events", params={"service": "order-service"}).json()
        assert any(e["type"] == "prediction.failure_risk" for e in stored)


def test_predict_and_capacity_endpoints(settings):
    with TestClient(create_app(settings)) as c:
        rising = [50 + i * 1.2 for i in range(30)]
        r = c.post("/api/v1/analytics/predict", json={"values": rising, "threshold": 100}).json()
        assert r["probability"] > 0.6 and r["baseline"]["steps_to_threshold"] is not None
        vals = [40 + 0.05 * i for i in range(100)]
        cap = c.post("/api/v1/analytics/capacity", json={"values": vals, "capacity": 60}).json()
        assert cap["trend_only_exhaustion_step"] is not None


def test_predict_without_model_is_503(settings, tmp_path):
    s = settings.model_copy(update={"failure_model_path": tmp_path / "none.json"})
    with TestClient(create_app(s)) as c:
        r = c.post("/api/v1/analytics/predict", json={"values": [1.0] * 10, "threshold": 5})
        assert r.status_code == 503
