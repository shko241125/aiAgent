"""계절성 인지 탐지 (M2-03 / 3.1).

운영 지표는 하루·주 단위 주기를 가진다. 평소 저녁 피크를 '이상'으로 울리면 알람 피로가 쌓인다.
방법: 같은 위상(phase)의 과거 주기 값들의 중앙값을 기대값으로 삼고,
      잔차(실제-기대)에 robust z-score 를 적용.
- 과거 데이터만 사용 → 스트리밍에서도 같은 결과 (미래 누설 없음)
- 주기를 모르면 자기상관(ACF)으로 추정한다
"""

from collections.abc import Sequence

import numpy as np

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector


def estimate_period(
    values: Sequence[float],
    min_period: int = 10,
    max_period: int | None = None,
    min_acf: float = 0.5,
) -> int | None:
    """자기상관(ACF)으로 주기 추정. 뚜렷한 주기가 없으면 None.

    주의: 매끄러운 곡선은 작은 지연에서도 ACF 가 1 에 가깝다(이웃 값이 비슷하므로).
    그래서 'ACF 가 처음 음수로 내려간 뒤'의 봉우리만 후보로 보고, 주기의 배수(2T, 3T)가
    아닌 가장 이른 강한 봉우리를 고른다. 스파이크가 가짜 상관을 만들지 않도록 먼저 이상치를 깎는다.
    """
    x = np.asarray(values, dtype=float)
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) or 1e-9
    x = np.clip(x, med - 5 * 1.4826 * mad, med + 5 * 1.4826 * mad)  # 이상치 클리핑
    x = x - x.mean()
    var = float(np.dot(x, x)) / len(x) or 1e-9
    max_period = min(max_period or len(x) // 3, len(x) - 2)  # 최소 3주기는 관측돼야 신뢰
    acf = [
        float(np.dot(x[:-lag], x[lag:])) / ((len(x) - lag) * var)  # 지연별 비편향 정규화
        for lag in range(1, max_period + 1)
    ]
    first_neg = next((i for i, a in enumerate(acf) if a < 0), None)
    if first_neg is None:
        return None  # 추세만 있고 주기 없음
    peaks = [
        i
        for i in range(max(first_neg, 1), len(acf) - 1)
        if acf[i] >= acf[i - 1] and acf[i] >= acf[i + 1] and acf[i] >= min_acf
    ]
    if not peaks:
        return None
    best = max(acf[i] for i in peaks)
    lag = next(i for i in peaks if acf[i] >= 0.8 * best) + 1  # 배수 주기보다 기본 주기 우선
    return lag if lag >= min_period else None


class SeasonalDetector(AnomalyDetector):
    name = "seasonal"

    def __init__(
        self, period: int | None = None, cycles: int = 4, threshold: float = 4.0, window: int = 120
    ) -> None:
        self.period = period
        self.cycles = cycles
        self.threshold = threshold
        self.window = window

    def residuals(self, x: np.ndarray, period: int) -> np.ndarray:
        res = np.full(len(x), np.nan)
        for i in range(period, len(x)):
            past = [x[i - k * period] for k in range(1, self.cycles + 1) if i - k * period >= 0]
            res[i] = x[i] - float(np.median(past))
        return res

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        x = np.asarray(values, dtype=float)
        period = self.period or estimate_period(x)
        if period is None or len(x) < 2 * period:
            from aiops.analytics.anomaly.statistical import RobustZScoreDetector

            return RobustZScoreDetector(threshold=self.threshold).detect(values)
        res = self.residuals(x, period)
        out = []
        for i in range(period + 10, len(x)):
            hist = res[max(period, i - self.window) : i]
            hist = hist[~np.isnan(hist)]
            med = float(np.median(hist))
            mad = float(np.median(np.abs(hist - med))) or 1e-9
            score = 0.6745 * (res[i] - med) / mad
            if abs(score) >= self.threshold:
                out.append(
                    Anomaly(
                        index=i,
                        value=float(x[i]),
                        score=abs(score),
                        expected=float(x[i] - res[i]),
                        method=self.name,
                    )
                )
        return out
