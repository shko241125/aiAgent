from datetime import timedelta

import numpy as np

from aiops.analytics.anomaly.ensemble import EnsembleDetector
from aiops.analytics.anomaly.statistical import EWMADetector, RobustZScoreDetector
from aiops.analytics.events import correlate, deduplicate, mine_patterns
from aiops.analytics.prediction import forecast_threshold_breach
from aiops.analytics.situation import SignalSet, SituationLevel, assess
from aiops.domain.models import OpsEvent, utcnow


def _series_with_spike(n=120, spike_at=100):
    rng = np.random.default_rng(0)
    x = 50 + rng.normal(0, 1, n)
    x[spike_at] = 90
    return x.tolist()


def test_detectors_find_spike():
    values = _series_with_spike()
    for det in (RobustZScoreDetector(), EWMADetector(), EnsembleDetector()):
        idx = [a.index for a in det.detect(values)]
        assert 100 in idx, det.name


def test_situation_levels():
    values = _series_with_spike()
    anomalies = EnsembleDetector().detect(values)
    calm = assess(SignalSet(service="s", series_length=len(values)))
    hot = assess(
        SignalSet(
            service="s",
            anomalies={"error_rate": anomalies},
            series_length=len(values),
            error_events=5,
            recent_changes=1,
        )
    )
    assert calm.level == SituationLevel.NORMAL
    assert hot.risk_score > calm.risk_score and hot.level != SituationLevel.NORMAL


def test_event_dedup_and_correlation():
    t0 = utcnow()
    events = [
        OpsEvent(timestamp=t0, source="cicd", service="order", type="deploy"),
        OpsEvent(timestamp=t0 + timedelta(minutes=2), source="p", service="order", type="latency"),
        OpsEvent(timestamp=t0 + timedelta(minutes=3), source="p", service="order", type="latency"),
        OpsEvent(timestamp=t0 + timedelta(hours=2), source="p", service="user", type="cpu"),
    ]
    deduped = deduplicate(events)
    assert len(deduped) == 3
    clusters = correlate(deduped)
    assert len(clusters) == 2
    assert mine_patterns(clusters)[0].pattern == ("deploy", "latency")


def test_forecast_rising_trend():
    values = [10 + i for i in range(30)]  # 매 step +1
    f = forecast_threshold_breach(values, threshold=50, horizon_steps=60)
    assert f.steps_to_threshold is not None and 10 < f.steps_to_threshold < 12
    assert f.risk_score > 0.5
    flat = forecast_threshold_breach([10.0] * 30, threshold=50)
    assert flat.steps_to_threshold is None and flat.risk_score == 0.0
