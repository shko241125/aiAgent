"""장애 예측 평가 (M4-06 / 3.4) — 합성 시계열로 학습·평가, 선형 외삽 기준선과 비교.

장애 정의: 지표가 임계치 이상으로 **5분 이상 지속**. 순간 스파이크는 장애가 아니다.
평가 단위는 '시계열(사건)' — 운영자가 체감하는 것은 시점별 정확도가 아니라
"장애 전에 미리 울렸나(적중·리드타임)" 와 "괜히 울렸나(오경보)" 이기 때문.

- 적중(recall@N): 장애 시각 f 기준 [f-3H, f-N] 안에 알람이 있었던 장애 비율 (N분 전 예측)
- 리드타임: 그 창 안 첫 알람 ~ 장애까지 분
- 오경보율(FAR): 장애가 없는 시계열 중 알람이 한 번이라도 울린 비율
- 너무 이른 알람(f-3H 이전)은 적중으로 치지 않는다 — 45분 전 '언젠가' 는 행동으로 안 이어진다

합성 데이터 — 시나리오와 모델을 같은 사람이 만들었으므로 **회귀 기준선**이다.
"""

import random
import statistics
from collections.abc import Callable

import numpy as np
from pydantic import BaseModel

from aiops.analytics.prediction import (
    HORIZON_MIN,
    PRED_FEATURES,
    WINDOW,
    failure_features,
    forecast_threshold_breach,
)
from aiops.analytics.situation_model import LogisticModel

T = 100.0  # 학습은 정규화 단위 (특징이 임계치 대비 비율이라 지표 종류와 무관)
LENGTH = 240
PERSIST = 5
FAILURE_KINDS = ["leak", "exp", "step_creep"]
# plateau: 포화 곡선(워밍업·힙 안정화 — 둔화하며 수렴)
# plateau_linear: 일정 기울기로 오르다 뚝 멈춤. 멈추기 전까지 누수(leak)와 과거 값이 **같다**
#   → 어떤 예측기도 과거 값만으론 구분 못 한다는 것을 드러내는 대조군
NORMAL_KINDS = ["seasonal", "spike", "plateau", "plateau_linear", "noisy"]


class LabeledSeries(BaseModel):
    kind: str
    values: list[float]
    failure_at: int | None  # 장애 시작 시각(index), 없으면 None


def failure_time(values: list[float], threshold: float = T, persist: int = PERSIST) -> int | None:
    run = 0
    for i, v in enumerate(values):
        run = run + 1 if v >= threshold else 0
        if run >= persist:
            return i - persist + 1
    return None


def make_series(kind: str, rng: random.Random) -> LabeledSeries:
    base = rng.uniform(30, 70)
    sigma = rng.uniform(0.8, 2.5)
    t = np.arange(LENGTH, dtype=float)
    noise = np.array([rng.gauss(0, sigma) for _ in range(LENGTH)])
    y = np.full(LENGTH, base)
    t0 = rng.randint(60, 150)
    if kind == "leak":  # 메모리 누수: 일정한 기울기로 상승
        y = y + np.clip(t - t0, 0, None) * (T - base) / rng.uniform(25, 80)
    elif kind == "exp":  # 큐 적체·재시도 폭주: 가속 상승
        k = rng.uniform(0.06, 0.12)
        span = rng.uniform(20, 60)  # 대략 이 시간 뒤 임계치 도달
        c = (T - base) / (np.exp(k * span) - 1)
        y = y + np.where(t > t0, c * (np.exp(k * np.clip(t - t0, 0, None)) - 1), 0)
    elif kind == "step_creep":  # 배포 직후 계단 상승 후 서서히 악화
        step = rng.uniform(8, 18)
        y = y + np.where(t > t0, step, 0)
        y = y + np.clip(t - t0, 0, None) * (T - base - step) / rng.uniform(40, 90)
    elif kind == "seasonal":  # 일과 패턴: 피크가 있지만 임계치 아래
        amp = rng.uniform(6, min(18, T - 12 - base))
        y = y + amp * np.sin(2 * np.pi * t / rng.uniform(60, 120) + rng.uniform(0, 6))
    elif kind == "spike":  # 순간 스파이크(배치 작업): 임계치를 넘어도 1~2분
        for _ in range(rng.randint(2, 4)):
            at = rng.randint(40, LENGTH - 3)
            y[at : at + rng.randint(1, 2)] = rng.uniform(T - 5, T + 15)
    elif kind == "plateau":  # 오르며 둔화해 수렴(워밍업) — 외삽은 초반 기울기에 속는다
        level = rng.uniform(T - 25, T - 8)
        tau = rng.uniform(8, 20)
        y = y + np.where(t > t0, (1 - np.exp(-np.clip(t - t0, 0, None) / tau)), 0) * (level - base)
    elif kind == "plateau_linear":  # 일정 기울기로 오르다 뚝 멈춤 — 멈추기 전엔 leak 과 동일
        level = rng.uniform(T - 25, T - 8)
        ramp = rng.uniform(15, 45)
        y = y + np.clip((t - t0) / ramp, 0, 1) * (level - base)
    elif kind == "noisy":  # 출렁이지만 지속 초과는 없음
        base = rng.uniform(55, 70)
        sigma = rng.uniform(3, 5)
        noise = np.array([rng.gauss(0, sigma) for _ in range(LENGTH)])
        y = np.full(LENGTH, base)
    values = [round(float(v), 3) for v in y + noise]
    f = failure_time(values)
    if f is not None:
        values = values[: min(LENGTH, f + PERSIST + 5)]
    return LabeledSeries(kind=kind, values=values, failure_at=f)


def generate(n_per_kind: int, seed: int) -> list[LabeledSeries]:
    rng = random.Random(seed)
    return [make_series(k, rng) for _ in range(n_per_kind) for k in FAILURE_KINDS + NORMAL_KINDS]


def _times(s: LabeledSeries) -> range:
    end = s.failure_at if s.failure_at is not None else len(s.values)
    return range(WINDOW, end)  # 장애가 시작된 뒤는 '예측' 이 아니다


def dataset(series: list[LabeledSeries], stride: int = 2) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for s in series:
        for t in _times(s)[::stride]:
            X.append(failure_features(s.values[: t + 1], T))
            y.append(int(s.failure_at is not None and t < s.failure_at <= t + HORIZON_MIN))
    return np.array(X), np.array(y)


AlarmFn = Callable[[LabeledSeries], list[int]]


def debounce(flags: list[tuple[int, bool]], k: int = 2) -> list[int]:
    """k 번 연속 조건을 만족해야 알람 — 한 점 튐으로 울리지 않게 (예측기·기준선 공통)."""
    out, run = [], 0
    for t, on in flags:
        run = run + 1 if on else 0
        if run >= k:
            out.append(t)
    return out


def series_probs(model: LogisticModel, s: LabeledSeries) -> list[tuple[int, float]]:
    X = np.array([failure_features(s.values[: t + 1], T) for t in _times(s)])
    if not len(X):
        return []
    z = (X - np.array(model.mean)) / np.array(model.std)
    p = 1 / (1 + np.exp(-(z @ np.array(model.weights) + model.bias)))
    return list(zip(_times(s), p.tolist(), strict=True))


def model_alarms(model: LogisticModel, theta: float, cache: dict | None = None) -> AlarmFn:
    """cache 를 주면 시계열별 확률을 재사용 (θ 스윕에서 특징을 매번 다시 계산하지 않게)."""
    cache = {} if cache is None else cache

    def fn(s: LabeledSeries) -> list[int]:
        if id(s) not in cache:
            cache[id(s)] = series_probs(model, s)
        return debounce([(t, p >= theta) for t, p in cache[id(s)]])

    return fn


def baseline_alarms(s: LabeledSeries) -> list[int]:
    flags = []
    for t in _times(s):
        f = forecast_threshold_breach(s.values[: t + 1], T, horizon_steps=HORIZON_MIN)
        flags.append((t, f.steps_to_threshold is not None and f.steps_to_threshold <= HORIZON_MIN))
    return debounce(flags)


class PredictionScore(BaseModel):
    name: str
    recall_at_lead: float  # [f-3H, f-N] 안에 알람이 있었던 장애 비율
    median_lead_min: float | None
    false_alarm_rate: float  # 장애 없는 시계열 중 알람이 울린 비율
    premature_rate: float  # 장애 시계열 중 f-3H 이전에 이미 울린 비율 (참고)
    by_kind: dict[str, float]  # 종류별 적중률(장애) / 오경보율(정상)


def score(name: str, alarm_fn: AlarmFn, series: list[LabeledSeries], lead: int = 5):
    hits, leads, premature, false_alarms = 0, [], 0, 0
    kinds: dict[str, list[int]] = {}
    failures = [s for s in series if s.failure_at is not None]
    normals = [s for s in series if s.failure_at is None]
    for s in series:
        alarms = alarm_fn(s)
        if s.failure_at is None:
            fa = int(bool(alarms))
            false_alarms += fa
            kinds.setdefault(s.kind, []).append(fa)
            continue
        f = s.failure_at
        window = [a for a in alarms if f - 3 * HORIZON_MIN <= a <= f - lead]
        premature += int(any(a < f - 3 * HORIZON_MIN for a in alarms))
        hit = int(bool(window))
        hits += hit
        kinds.setdefault(s.kind, []).append(hit)
        if window:
            leads.append(f - window[0])
    return PredictionScore(
        name=name,
        recall_at_lead=round(hits / max(1, len(failures)), 3),
        median_lead_min=statistics.median(leads) if leads else None,
        false_alarm_rate=round(false_alarms / max(1, len(normals)), 3),
        premature_rate=round(premature / max(1, len(failures)), 3),
        by_kind={k: round(sum(v) / len(v), 3) for k, v in kinds.items()},
    )


class PredictionEvalReport(BaseModel):
    lead_min: int
    horizon_min: int
    theta: float
    train_series: int
    test_series: int
    model_score: PredictionScore
    baseline_score: PredictionScore
    theta_sweep: list[tuple[float, float, float]]  # (θ, 검증 적중률, 검증 오경보율)
    model: LogisticModel


def evaluate_prediction(
    n_train: int = 30, n_val: int = 15, n_test: int = 30, lead: int = 5, max_far: float = 0.1
) -> PredictionEvalReport:
    train, val, test = generate(n_train, 101), generate(n_val, 202), generate(n_test, 303)
    X, y = dataset(train)
    model = LogisticModel.fit(X, y)
    model.features = PRED_FEATURES
    # θ 는 검증 세트에서: 오경보율 상한 안에서 적중률 최대 (평가 세트는 마지막에 한 번만 본다)
    sweep, cache = [], {}
    for theta in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        sc = score("val", model_alarms(model, theta, cache), val, lead)
        sweep.append((theta, sc.recall_at_lead, sc.false_alarm_rate))
    ok = [s for s in sweep if s[2] <= max_far] or sweep
    theta = max(ok, key=lambda s: (s[1], -s[2], s[0]))[0]
    return PredictionEvalReport(
        lead_min=lead,
        horizon_min=HORIZON_MIN,
        theta=theta,
        train_series=len(train),
        test_series=len(test),
        model_score=score("learned", model_alarms(model, theta), test, lead),
        baseline_score=score("linear-extrapolation", baseline_alarms, test, lead),
        theta_sweep=sweep,
        model=model,
    )
