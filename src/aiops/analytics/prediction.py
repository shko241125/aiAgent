"""장애 발생 예측 및 위험도 분석 (3.4).

현재: 선형 추세 외삽으로 임계치 도달 예상 시간(time-to-threshold) 계산.
TODO(3.4): 과거 인시던트 라벨 기반 분류 모델(GBM 등)로 'N분 내 장애 확률' 예측,
           계절성 고려 예측(Prophet/ARIMA), 용량 계획(capacity forecasting).
"""

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel


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
