"""변화점 탐지 — CUSUM (M2-06 / 3.5).

점 단위 탐지기(z-score)는 '튀는 값'에는 강하지만 '서서히 변하는 값(drift)'에는 약하다
(M2-03 평가에서 drift F1 ≤ 0.63, 지연 12~18분). CUSUM 은 기준선에서 벗어난 작은 편차를
누적(cumulative sum)해 임계치를 넘으면 변화로 판정한다 — 작은 변화도 쌓이면 잡는다.

    S⁺ₜ = max(0, S⁺ₜ₋₁ + zₜ − k),  S⁻ₜ = max(0, S⁻ₜ₋₁ − zₜ − k),  S > h 이면 변화점
    (z: 기준선 대비 표준화 편차, k: 허용 편차(drift), h: 결정 임계치)
"""

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector


class ChangePoint(BaseModel):
    index: int  # 변화가 감지된 시점
    start: int  # 누적이 시작된 (추정) 변화 시작점
    direction: str  # up | down
    magnitude: float  # 기준선 대비 변화량 (원 단위)
    relative: float  # 기준선 평균 대비 변화 비율


def _baseline(ref: np.ndarray) -> tuple[float, float]:
    """기준선 평균·표준편차. 이상치를 깎은 뒤 std 를 쓴다.

    MAD 는 견고하지만 표본이 적으면 흔들린다(30점에서 σ 를 40% 과소추정한 사례) → σ 과소추정은
    모든 z 를 부풀려 CUSUM 헛알람을 낳는다. 클리핑 후 std 가 효율과 견고성의 절충.
    """
    med = float(np.median(ref))
    mad = float(np.median(np.abs(ref - med))) * 1.4826 or 1e-9
    clipped = np.clip(ref, med - 4 * mad, med + 4 * mad)
    return float(clipped.mean()), float(clipped.std()) or 1e-9


def cusum(
    values: Sequence[float],
    k: float = 0.5,
    h: float = 8.0,
    baseline: int = 60,
    reset: bool = True,
    cooldown: int = 30,
    rewarm: int = 10,
) -> list[ChangePoint]:
    """rewarm: 변화 감지 후 새 구간에서 평균·표준편차를 다시 추정할 표본 수.

    장애는 흔히 '곱셈형'이다(에러율 ×8 이면 잡음도 ×8). 옛 σ 를 그대로 쓰면 새 구간의
    정상 잡음이 변화로 보인다 → 새 구간에서 μ·σ 를 모두 다시 잡는다.
    """
    x = np.asarray(values, dtype=float)
    if len(x) <= baseline:
        return []
    mu, sigma = _baseline(x[:baseline])
    quiet_until = -1
    warm_from: int | None = None
    pos = neg = 0.0
    pos_start = neg_start = baseline
    out: list[ChangePoint] = []
    for i in range(baseline, len(x)):
        if warm_from is not None:  # 새 구간 기준선 재추정 중
            if i - warm_from + 1 < rewarm:
                continue
            mu, sigma = _baseline(x[warm_from : i + 1])
            warm_from = None
            pos = neg = 0.0
            continue
        z = (x[i] - mu) / sigma
        if pos == 0:
            pos_start = i
        if neg == 0:
            neg_start = i
        pos = max(0.0, pos + z - k)
        neg = max(0.0, neg - z - k)
        if (pos > h or neg > h) and i > quiet_until:
            up = pos > h
            start = pos_start if up else neg_start
            out.append(
                ChangePoint(
                    index=i,
                    start=start,
                    direction="up" if up else "down",
                    magnitude=round(float(x[i] - mu), 3),
                    relative=round(float(x[i] - mu) / (abs(mu) or 1e-9), 3),
                )
            )
            if not reset:
                break
            warm_from = start  # 변화 시작점부터의 표본으로 새 기준선을 잡는다
            pos = neg = 0.0
            quiet_until = i + cooldown
    return out


class CUSUMDetector(AnomalyDetector):
    """탐지기 평가 하네스(M2-03)에 넣기 위한 어댑터 — 변화점을 이상 지점으로 보고."""

    name = "cusum"

    def __init__(self, k: float = 0.5, h: float = 8.0, baseline: int = 60) -> None:
        self.k, self.h, self.baseline = k, h, baseline

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        return [
            Anomaly(
                index=c.index,
                value=float(values[c.index]),
                score=abs(c.magnitude),
                method=self.name,
            )
            for c in cusum(values, self.k, self.h, self.baseline)
        ]
