"""통계 기반 이상 탐지기 (3.1) — 학습 데이터 없이 바로 쓸 수 있는 1차 방어선."""

from collections.abc import Sequence

import numpy as np

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector


class RobustZScoreDetector(AnomalyDetector):
    """이동 창(window)의 median/MAD 기반 z-score.

    평균·표준편차 대신 중앙값·MAD 를 써서, 과거 창에 섞인 스파이크에 기준선이 끌려가지 않는다.
    """

    name = "robust_zscore"

    def __init__(self, window: int = 60, threshold: float = 3.5, min_history: int = 10) -> None:
        self.window = window
        self.threshold = threshold
        self.min_history = min_history

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        x = np.asarray(values, dtype=float)
        out: list[Anomaly] = []
        for i in range(self.min_history, len(x)):
            hist = x[max(0, i - self.window) : i]
            med = float(np.median(hist))
            mad = float(np.median(np.abs(hist - med))) or 1e-9
            score = 0.6745 * (x[i] - med) / mad  # 0.6745: MAD → 표준편차 환산 상수
            if abs(score) >= self.threshold:
                out.append(
                    Anomaly(
                        index=i, value=float(x[i]), score=abs(score), expected=med, method=self.name
                    )
                )
        return out


class EWMADetector(AnomalyDetector):
    """지수가중이동평균(EWMA) 대비 편차 탐지 — 완만한 추세 변화에 적응."""

    name = "ewma"

    def __init__(self, alpha: float = 0.3, threshold: float = 3.0, warmup: int = 10) -> None:
        self.alpha = alpha
        self.threshold = threshold
        self.warmup = warmup

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        x = np.asarray(values, dtype=float)
        if len(x) <= self.warmup:
            return []
        mean = float(np.mean(x[: self.warmup]))
        var = float(np.var(x[: self.warmup])) or 1e-9
        out: list[Anomaly] = []
        for i in range(self.warmup, len(x)):
            std = var**0.5
            score = abs(x[i] - mean) / std
            if score >= self.threshold:
                out.append(
                    Anomaly(
                        index=i, value=float(x[i]), score=score, expected=mean, method=self.name
                    )
                )
                continue  # 이상치는 기준선 갱신에 반영하지 않는다
            diff = x[i] - mean
            mean += self.alpha * diff
            var = (1 - self.alpha) * (var + self.alpha * diff * diff)
        return out


class ThresholdDetector(AnomalyDetector):
    """정적 임계치 — SLO/운영 규칙 기반 탐지."""

    name = "threshold"

    def __init__(self, upper: float | None = None, lower: float | None = None) -> None:
        self.upper, self.lower = upper, lower

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        out = []
        for i, v in enumerate(values):
            if self.upper is not None and v > self.upper:
                out.append(
                    Anomaly(
                        index=i,
                        value=v,
                        score=v / self.upper,
                        expected=self.upper,
                        method=self.name,
                    )
                )
            elif self.lower is not None and v < self.lower:
                out.append(
                    Anomaly(
                        index=i,
                        value=v,
                        score=self.lower / max(v, 1e-9),
                        expected=self.lower,
                        method=self.name,
                    )
                )
        return out
