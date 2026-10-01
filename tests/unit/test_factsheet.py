"""M2-06 변화점(CUSUM) · 메트릭 상관 · fact sheet."""

import numpy as np

from aiops.analytics.anomaly.synthetic import generate
from aiops.analytics.changepoint import cusum
from aiops.analytics.factsheet import build_fact_sheet, compression, correlate_metrics
from aiops.integrations.simulated import ChangeEvent, Fault, FaultScenario, SimulatedOpsSource


def test_cusum_catches_drift_and_stays_quiet_on_noise():
    drift = generate("drift", seed=3)
    start = drift.events[0][0]
    cps = cusum(drift.values, h=12)
    assert cps and cps[0].direction == "up" and start <= cps[0].index <= start + 40
    assert cusum(generate("normal", seed=1).values, h=12) == []


def test_cusum_rebaselines_after_multiplicative_shift():
    rng = np.random.default_rng(0)
    x = np.concatenate([10 + rng.normal(0, 0.5, 100), 80 + rng.normal(0, 4, 100)])  # 잡음도 ×8
    cps = cusum(x, h=12)
    assert len(cps) == 1 and cps[0].direction == "up"  # 새 구간 잡음을 변화로 오인하지 않음


def test_correlation_uses_co_movement_not_trend():
    t = np.arange(60)
    rng = np.random.default_rng(1)
    shared = rng.normal(size=60).cumsum()
    corr = correlate_metrics(
        {"a": list(shared), "b": list(shared * 2 + 5), "c": list(t * 1.0 + rng.normal(size=60))}
    )
    pairs = {(c.a, c.b) for c in corr}
    assert ("a", "b") in pairs and (
        "a",
        "c",
    ) not in pairs  # 둘 다 오르는 추세여도 함께 움직이진 않음


async def test_fact_sheet_reports_change_onset_and_compresses():
    correct = 0
    for seed in range(10):
        src = SimulatedOpsSource(seed=seed)
        src.apply_scenario(
            FaultScenario(
                id="x",
                alert_service="order-service",
                faults=[
                    Fault(
                        service="order-service",
                        metric="latency_p95_ms",
                        magnitude=3,
                        onset_min_ago=15,
                    )
                ],
                changes=[ChangeEvent(service="order-service", minutes_ago=20, message="v9 배포")],
            )
        )
        sheet, raw = await build_fact_sheet(src, "order-service")
        lat = next(m for m in sheet.metrics if m.metric == "latency_p95_ms")
        correct += lat.change_direction == "up" and abs(lat.change_min_ago - 15) <= 2
    assert correct >= 9
    text = sheet.to_prompt()
    assert "v9 배포" in text and "15분 전부터 up" in text
    assert compression(sheet, raw)["reduction_pct"] >= 60
