"""M2-02 이벤트 수집 — Alertmanager/변경 웹훅 파싱, DB 저장소가 RCA 의 change 신호가 된다."""

from datetime import timedelta

from aiops.analytics.rca import RCAAnalyzer
from aiops.db.models import create_engine, create_sessionmaker, init_db
from aiops.domain.models import Severity, utcnow
from aiops.integrations.composite import CompositeOpsSource
from aiops.integrations.events import (
    AlertmanagerPayload,
    ChangePayload,
    SqlEventStore,
    alertmanager_to_events,
    change_to_event,
)
from aiops.integrations.simulated import Fault, FaultScenario, SimulatedOpsSource

AM = {
    "version": "4",
    "status": "firing",
    "commonLabels": {"namespace": "prod"},
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "f1",
            "startsAt": "2026-10-01T01:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "labels": {
                "alertname": "HighErrorRate",
                "service": "order-service",
                "severity": "critical",
            },
            "annotations": {"summary": "order-service 5xx > 5%"},
        },
        {
            "status": "resolved",
            "fingerprint": "f2",
            "startsAt": "2026-10-01T00:00:00Z",
            "endsAt": "2026-10-01T00:30:00Z",
            "labels": {"alertname": "PodRestart", "app": "user-service", "severity": "warning"},
        },
    ],
}


def test_alertmanager_parsing():
    firing, resolved = alertmanager_to_events(AlertmanagerPayload.model_validate(AM))
    assert (firing.service, firing.type, firing.severity) == (
        "order-service",
        "alert.HighErrorRate",
        Severity.CRITICAL,
    )
    assert firing.message == "order-service 5xx > 5%"
    assert firing.attributes["labels"]["namespace"] == "prod"  # commonLabels 병합
    assert resolved.service == "user-service" and resolved.type.endswith(".resolved")


async def test_change_event_becomes_rca_candidate(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/e.db")
    await init_db(engine)
    store = SqlEventStore(create_sessionmaker(engine))
    ev = change_to_event(
        ChangePayload(
            service="order-service", version="v9.9", timestamp=utcnow() - timedelta(minutes=14)
        )
    )
    assert await store.add([ev]) == [ev]
    assert await store.add([ev]) == []  # 재전송 → 무시 (멱등)

    sim = SimulatedOpsSource()
    sim.apply_scenario(
        FaultScenario(
            id="x",
            alert_service="order-service",
            faults=[
                Fault(service="order-service", metric="error_rate", magnitude=10, onset_min_ago=10)
            ],
        )
    )
    source = CompositeOpsSource(metrics=sim, logs=sim, events=store, topology=sim)
    top = (await RCAAnalyzer(source).analyze("order-service"))[0]
    assert top.kind == "change" and "v9.9" in top.cause
    await engine.dispose()
