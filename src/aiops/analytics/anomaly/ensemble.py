"""여러 탐지기의 결과를 투표(voting)로 결합 — 단일 탐지기의 오탐을 줄인다."""

from collections import defaultdict
from collections.abc import Sequence

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector
from aiops.analytics.anomaly.statistical import EWMADetector, RobustZScoreDetector


class EnsembleDetector(AnomalyDetector):
    name = "ensemble"

    def __init__(self, detectors: list[AnomalyDetector] | None = None, min_votes: int = 2) -> None:
        self.detectors = detectors or [RobustZScoreDetector(), EWMADetector()]
        self.min_votes = min(min_votes, len(self.detectors))

    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        votes: dict[int, list[Anomaly]] = defaultdict(list)
        for d in self.detectors:
            for a in d.detect(values):
                votes[a.index].append(a)
        out = []
        for idx, hits in sorted(votes.items()):
            if len(hits) >= self.min_votes:
                out.append(
                    Anomaly(
                        index=idx,
                        value=hits[0].value,
                        score=max(h.score for h in hits),
                        expected=hits[0].expected,
                        method="+".join(h.method for h in hits),
                    )
                )
        return out


def default_detector() -> AnomalyDetector:
    return EnsembleDetector()
