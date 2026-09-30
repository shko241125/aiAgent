"""ML 기반 이상 탐지 (3.1) — `pip install .[ml]` 필요.

TODO(3.1): 다변량(메트릭 여러 개 동시) 입력, 모델 저장/버전관리, 서비스별 모델 학습 파이프라인,
           시계열 특화 모델(Prophet, LSTM-AE, Transformer 계열) 비교 실험.
"""

from collections.abc import Sequence

import numpy as np

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector


class IsolationForestDetector(AnomalyDetector):
    name = "isolation_forest"

    def __init__(self, window: int = 5, contamination: float = 0.02, seed: int = 0) -> None:
        self.window = window
        self.contamination = contamination
        self.seed = seed

    def _features(self, x: np.ndarray) -> np.ndarray:
        # 값 + 직전 window 대비 변화량을 특징으로 사용 (sliding window embedding)
        diffs = np.concatenate([[0.0], np.diff(x)])
        roll = np.array([x[max(0, i - self.window) : i + 1].mean() for i in range(len(x))])
        return np.column_stack([x, diffs, x - roll])

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        from sklearn.ensemble import IsolationForest  # 선택 의존성

        x = np.asarray(values, dtype=float)
        feats = self._features(x)
        model = IsolationForest(contamination=self.contamination, random_state=self.seed)
        labels = model.fit_predict(feats)
        scores = -model.score_samples(feats)
        return [
            Anomaly(index=int(i), value=float(x[i]), score=float(scores[i]), method=self.name)
            for i in np.where(labels == -1)[0]
        ]
