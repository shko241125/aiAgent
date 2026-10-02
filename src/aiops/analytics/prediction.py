"""장애 발생 예측 및 위험도 분석 (3.4).

두 가지 질문에 답한다.
1) "N분 안에 이 지표가 임계치를 넘어 **지속**될 확률은?" — 학습형(로지스틱) 장애 예측 (M4-06)
   기준선: 선형 추세 외삽으로 임계치 도달 시간 계산 (`forecast_threshold_breach`)
   외삽은 '오르고 있다' 만 본다 → 오르다 멈추는 정상 패턴(배포 후 워밍업 등)에도 울린다.
   학습 모델은 추세의 **일관성·가속·여유분**을 함께 본다.
2) "용량이 언제 바닥나는가?" — 추세 + 계절성 분해 용량 예측 (`forecast_capacity`)
   추세만 보면 평균이 한도에 닿는 날을 말하지만, 실제로는 **피크**가 먼저 닿는다.

TODO(3.4): 실 인시던트 이력 라벨로 재학습 (현재 합성 시계열 — 회귀 기준선), 다변량 특징.
"""

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel

from aiops.analytics.anomaly.seasonal import estimate_period
from aiops.analytics.situation_model import LogisticModel


class RiskForecast(BaseModel):
    slope_per_step: float
    current: float
    threshold: float
    steps_to_threshold: float | None  # None: 도달 예상 없음
    risk_score: float  # 0~1


def forecast_threshold_breach(
    values: Sequence[float], threshold: float, lookback: int = 30, horizon_steps: int = 60
) -> RiskForecast:
    y = np.asarray(values[-lookback:], dtype=float)
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1) if len(y) >= 2 else (0.0, float(y[-1]))
    current = float(intercept + slope * (len(y) - 1))
    if current >= threshold:
        return RiskForecast(
            slope_per_step=float(slope),
            current=current,
            threshold=threshold,
            steps_to_threshold=0.0,
            risk_score=1.0,
        )
    steps = (threshold - current) / slope if slope > 0 else None
    if steps is None or steps > horizon_steps:
        risk = 0.0 if steps is None else max(0.0, 1 - steps / (horizon_steps * 4))
    else:
        risk = 1 - steps / horizon_steps * 0.5  # horizon 안이면 최소 0.5
    return RiskForecast(
        slope_per_step=float(slope),
        current=current,
        threshold=threshold,
        steps_to_threshold=None if steps is None else float(steps),
        risk_score=round(risk, 3),
    )


# ---------------------------------------------------------------------------
# 학습형 장애 예측 (M4-06)
# ---------------------------------------------------------------------------
HORIZON_MIN = 15  # "15분 안에 장애" 를 예측한다 (1 step = 1분)
WINDOW = 30  # 특징 계산에 쓰는 최근 구간
PRED_FEATURES = [
    "margin",  # 임계치까지 남은 여유 (현재값=최근 5분 중앙값, 임계치 대비 비율)
    "rise_short",  # 최근 10분 기울기(Theil–Sen)로 horizon 동안 오를 양 (임계치 대비)
    "rise_long",  # 최근 30분 기울기(Theil–Sen) 기준
    "accel",  # rise_short - rise_long (양수 = 가속, 음수 = 둔화 → 포화·안정화 신호)
    "trend_r2",  # 최근 30분 선형 추세의 설명력 (일관된 상승인가, 출렁임인가)
    "noise",  # 추세 제거 잔차의 MAD (임계치 대비)
    "reach",  # rise_short / margin — horizon 안에 여유분을 다 쓰는가 (1 이상이면 도달)
    "recent_p80",  # 최근 10분 80 백분위 (임계치 대비) — 최댓값은 스파이크 한 점에 휘둘린다
    "reach_hit",  # reach >= 1 (외삽 기준선의 판정을 특징으로 — 선형 모델에 비선형 경계 제공)
    "reach_long_hit",  # 30분 기울기로도 horizon 안에 도달하는가
    "snr",  # 상승량 / 잡음 — 잡음 속 우연한 기울기 구분
]


def _fit(y: np.ndarray) -> tuple[float, float, float]:
    """OLS 기울기·절편·R². polyfit 보다 가볍게 (학습 시 수만 번 호출)."""
    x = np.arange(len(y), dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum()) or 1e-9
    slope = float(((x - xm) * (y - ym)).sum()) / sxx
    intercept = ym - slope * xm
    resid = y - (intercept + slope * x)
    sst = float(((y - ym) ** 2).sum())
    r2 = 1 - float((resid**2).sum()) / sst if sst > 1e-9 else 0.0
    return slope, intercept, r2


def _theil_sen(y: np.ndarray, step: int = 1) -> float:
    """두 점씩 짝지은 기울기들의 중앙값 — 스파이크 몇 개에 흔들리지 않는 강건한 기울기."""
    i, j = np.triu_indices(len(y), 1)
    return float(np.median((y[j] - y[i]) / (j - i))) / step


def failure_features(values: Sequence[float], threshold: float) -> np.ndarray:
    """최근 값들 → 특징 벡터. 모든 크기는 임계치로 나눠 지표 종류(%, ms)와 무관하게 만든다.

    평균·최댓값·최소제곱 대신 중앙값·백분위·Theil–Sen 을 쓴다: 순간 스파이크(배치 작업)가
    '임계치에 가깝다·급상승' 으로 보이면 오경보가 난다 (첫 버전에서 실제로 발생).
    """
    y = np.asarray(values[-WINDOW:], dtype=float)
    if len(y) < WINDOW:  # 부족하면 첫 값으로 앞을 채운다 (평탄 가정)
        y = np.concatenate([np.full(WINDOW - len(y), y[0]), y])
    t = float(threshold) or 1.0
    current = float(np.median(y[-5:]))
    s_short = _theil_sen(y[-10:])
    s_long = _theil_sen(y[::2], step=2)  # 30점 전체 짝(435개) 대신 격점 표본으로 비용 절감
    fit_slope, fit_b, r2 = _fit(y)
    resid = y - (fit_b + fit_slope * np.arange(len(y)))
    noise = 1.4826 * float(np.median(np.abs(resid - np.median(resid)))) / t
    margin = (t - current) / t
    rise_short = s_short * HORIZON_MIN / t
    rise_long = s_long * HORIZON_MIN / t
    reach = rise_short / max(margin, 0.01) if rise_short > 0 else 0.0
    reach_long = rise_long / max(margin, 0.01) if rise_long > 0 else 0.0
    return np.array(
        [
            margin,
            rise_short,
            rise_long,
            rise_short - rise_long,
            max(r2, 0.0),
            noise,
            min(reach, 5.0),
            float(np.percentile(y[-10:], 80)) / t,
            float(reach >= 1),
            float(reach_long >= 1),
            min(rise_short / (noise + 0.01), 20.0),
        ]
    )


class FailurePrediction(BaseModel):
    probability: float  # horizon_min 안에 임계치 지속 초과(장애)가 날 확률
    horizon_min: int = HORIZON_MIN
    current: float
    threshold: float
    eta_min: float | None  # 최근 기울기로 단순 계산한 도달 예상 시간 (참고용)
    factors: list[str]  # 판단 근거 (특징 기여도 상위)
    baseline: RiskForecast  # 선형 외삽 기준선 — 나란히 보여 준다


def predict_failure(
    model: LogisticModel, values: Sequence[float], threshold: float
) -> FailurePrediction:
    x = failure_features(values, threshold)
    p = model.predict_proba(x)
    slope = x[1] * threshold / HORIZON_MIN
    current = float(np.median(np.asarray(values[-5:], dtype=float)))
    eta = (threshold - current) / slope if slope > 0 and current < threshold else None
    return FailurePrediction(
        probability=round(p, 4),
        current=round(current, 3),
        threshold=threshold,
        eta_min=None if eta is None else round(float(eta), 1),
        factors=[f"{n} {c:+.2f}" for n, c in model.explain(x)],
        baseline=forecast_threshold_breach(values, threshold, horizon_steps=HORIZON_MIN),
    )


# ---------------------------------------------------------------------------
# 용량 예측 (M4-06)
# ---------------------------------------------------------------------------
class CapacityForecast(BaseModel):
    period: int | None  # 추정 주기 (step), 없으면 추세만
    trend_per_step: float
    exhaustion_step: int | None  # 예측(추세+계절성)이 처음 용량에 닿는 step (미래 기준)
    exhaustion_step_upper: int | None  # 상단 90% 구간 기준 — 보수적 계획용
    trend_only_exhaustion_step: int | None  # 계절성을 무시했을 때 (비교용)
    forecast: list[float]  # horizon 동안 예측값


def forecast_capacity(
    values: Sequence[float], capacity: float, horizon: int = 1440, period: int | None = None
) -> CapacityForecast:
    """y = 선형 추세 + 위상별 계절 성분 + 잡음. 잔차 표준편차로 상단 구간(×1.28 ≈ 90%)을 만든다."""
    y = np.asarray(values, dtype=float)
    n = len(y)
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    period = period or estimate_period(y - (intercept + slope * x))
    profile, seasonal = None, np.zeros(n)
    # 백피팅: 직선을 계절 성분이 섞인 원본에 바로 맞추면 사인파와 x 의 상관 때문에 기울기가
    # 크게 틀어진다(테스트에서 0.02 → 0.003). 계절 성분 추정 ↔ 계절 제거 후 추세 재적합을 반복
    for _ in range(3 if period else 0):
        detrended = y - (intercept + slope * x)
        profile = np.array([detrended[i::period].mean() for i in range(period)])
        profile -= profile.mean()  # 계절 성분은 평균 0 — 수준은 추세가 맡는다
        seasonal = profile[np.arange(n) % period]
        slope, intercept = np.polyfit(x, y - seasonal, 1)
    sigma = float((y - (intercept + slope * x) - seasonal).std())
    fx = np.arange(n, n + horizon, dtype=float)
    trend_f = intercept + slope * fx
    seas_f = profile[fx.astype(int) % period] if profile is not None else np.zeros(horizon)
    forecast = trend_f + seas_f

    def first(arr: np.ndarray) -> int | None:
        hit = np.nonzero(arr >= capacity)[0]
        return int(hit[0]) + 1 if len(hit) else None

    return CapacityForecast(
        period=period,
        trend_per_step=round(float(slope), 6),
        exhaustion_step=first(forecast),
        exhaustion_step_upper=first(forecast + 1.28 * sigma),
        trend_only_exhaustion_step=first(trend_f),
        forecast=[round(float(v), 3) for v in forecast],
    )
