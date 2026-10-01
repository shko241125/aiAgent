"""M2-03 이상 탐지 평가 · 계절성 탐지 · 스트리밍."""

import math

from aiops.analytics.anomaly.ensemble import EnsembleDetector
from aiops.analytics.anomaly.evaluation import evaluate_detector, score_series
from aiops.analytics.anomaly.seasonal import SeasonalDetector, estimate_period
from aiops.analytics.anomaly.streaming import StreamingEWMA
from aiops.analytics.anomaly.synthetic import LabeledSeries, dataset, generate


def test_event_level_scoring():
    s = LabeledSeries(kind="x", values=[0.0] * 100, events=[(10, 10), (50, 60)])
    detected, false_alarms, delays = score_series([12, 55, 56, 80, 81, 90], s)
    assert detected == 2 and delays == [2, 5]
    assert false_alarms == 2  # 80·81 은 한 묶음, 90 은 별도


def test_period_estimation_ignores_harmonics_and_spikes():
    assert abs(estimate_period(generate("seasonal", seed=1).values) - 120) <= 2
    assert estimate_period(generate("spike", seed=1).values) is None
    assert estimate_period([math.sin(i / 3) for i in range(20)]) is None  # 3주기 미만


def test_seasonal_detector_beats_ensemble():
    """기준선: 계절성 시계열 헛알람을 크게 줄이면서 스파이크는 놓치지 않는다."""
    data = dataset(seeds=4)
    seasonal = evaluate_detector("s", lambda s: SeasonalDetector(), data)
    ensemble = evaluate_detector("e", lambda s: EnsembleDetector(), data)
    assert seasonal.overall_f1 >= ensemble.overall_f1 + 0.15
    assert seasonal.kind("seasonal").false_alarms * 3 <= ensemble.kind("seasonal").false_alarms
    assert seasonal.kind("seasonal_spike").recall >= 0.9


def test_streaming_ewma_matches_offline_intuition():
    det = StreamingEWMA(warmup=20)
    s = generate("spike", seed=2)
    hits = [i for i, v in enumerate(s.values) if det.update(v)]
    assert {e[0] for e in s.events} <= set(hits)
