"""M4-06 장애 예측 — 강건한 특징, 학습 모델 판단, 용량 예측(피크 기준)."""

import math
import random

import numpy as np

from aiops.analytics.prediction import (
    PRED_FEATURES,
    failure_features,
    forecast_capacity,
    predict_failure,
)
from aiops.analytics.situation_model import LogisticModel
from aiops.core.config import Settings
from aiops.evals.prediction import failure_time, make_series

MODEL = LogisticModel.load(Settings().failure_model_path)
F = {name: i for i, name in enumerate(PRED_FEATURES)}


def flat(n=60, base=50.0, seed=1):
    rng = random.Random(seed)
    return [base + rng.gauss(0, 1) for _ in range(n)]


def test_failure_definition_needs_persistence():
    assert failure_time([50, 101, 102, 50, 50, 50, 50]) is None  # 순간 스파이크는 장애 아님
    assert failure_time([50, 101, 102, 103, 104, 105, 106]) == 1


def test_features_ignore_single_spikes():
    calm = flat()
    spiky = list(calm)
    spiky[-3] = spiky[-2] = 115.0  # 배치 작업 스파이크 2점
    a, b = failure_features(calm, 100), failure_features(spiky, 100)
    assert b[F["reach_hit"]] == a[F["reach_hit"]] == 0
    assert abs(b[F["margin"]] - a[F["margin"]]) < 0.05  # 중앙값 기반 현재값


def test_model_ranks_leak_above_plateau_and_flat():
    assert MODEL is not None, "python scripts/eval_prediction.py --save"
    leak = [50 + max(0, i - 20) * 1.1 + random.Random(i).gauss(0, 1) for i in range(60)]
    # 포화 곡선: 처음엔 빠르게 오르지만 둔화하며 수렴 (워밍업)
    plateau = [50 + 30 * (1 - math.exp(-max(0, i - 20) / 10)) for i in range(60)]
    p_leak = predict_failure(MODEL, leak, 100).probability
    p_plateau = predict_failure(MODEL, plateau, 100).probability
    p_flat = predict_failure(MODEL, flat(), 100).probability
    assert p_leak > 0.6 > p_plateau and p_flat < 0.05
    pred = predict_failure(MODEL, leak, 100)
    assert pred.eta_min is not None and pred.factors and pred.baseline.steps_to_threshold


def test_capacity_peak_hits_before_trend():
    """평균(추세)만 보면 늦게 닿지만, 일일 피크는 훨씬 먼저 한도에 닿는다."""
    period, n = 48, 48 * 6
    vals = [50 + 0.02 * i + 15 * math.sin(2 * math.pi * i / period) for i in range(n)]
    fc = forecast_capacity(vals, capacity=80, horizon=2000)
    assert fc.period == period
    assert fc.exhaustion_step is not None and fc.trend_only_exhaustion_step is not None
    assert fc.exhaustion_step < fc.trend_only_exhaustion_step
    assert fc.exhaustion_step_upper <= fc.exhaustion_step


def test_synthetic_kinds_have_expected_labels():
    rng = random.Random(5)
    for kind in ("leak", "exp", "step_creep"):
        assert make_series(kind, rng).failure_at is not None
    for kind in ("seasonal", "plateau", "plateau_linear", "noisy", "spike"):
        assert make_series(kind, rng).failure_at is None
    assert np.isfinite(failure_features([1.0] * 3, 100)).all()  # 짧은 입력도 안전
