"""스트리밍(온라인) 탐지 (M2-03 / 3.1) — 값이 하나 들어올 때마다 O(1) 로 판정.

배치 탐지기는 매번 전체 창을 다시 계산한다. 수천 개 시계열을 실시간 감시하려면
상태(평균·분산)를 갱신하며 새 값만 보는 온라인 방식이 필요하다.
"""

from abc import ABC, abstractmethod

from aiops.analytics.anomaly.base import Anomaly


class StreamingDetector(ABC):
    name = "streaming"

    @abstractmethod
    def update(self, value: float) -> Anomaly | None:
        """새 관측값을 반영하고, 이상이면 Anomaly 를 반환."""


class StreamingEWMA(StreamingDetector):
    """지수가중 평균·분산을 온라인 갱신. 이상치는 기준선에 반영하지 않는다(오염 방지)."""

    name = "streaming_ewma"

    def __init__(self, alpha: float = 0.1, threshold: float = 4.0, warmup: int = 20) -> None:
        self.alpha, self.threshold, self.warmup = alpha, threshold, warmup
        self.n = 0
        self.mean = 0.0
        self.var = 0.0

    def update(self, value: float) -> Anomaly | None:
        i = self.n
        self.n += 1
        if i < self.warmup:  # 웰포드(Welford) 방식으로 초기 평균·분산
            delta = value - self.mean
            self.mean += delta / self.n
            self.var += (delta * (value - self.mean) - self.var) / self.n
            return None
        std = self.var**0.5 or 1e-9
        score = abs(value - self.mean) / std
        if score >= self.threshold:
            return Anomaly(index=i, value=value, score=score, expected=self.mean, method=self.name)
        diff = value - self.mean
        self.mean += self.alpha * diff
        self.var = (1 - self.alpha) * (self.var + self.alpha * diff * diff)
        return None
