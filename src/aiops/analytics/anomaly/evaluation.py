"""이상 탐지 평가 (M2-03 / 3.1) — 이벤트 단위 Precision/Recall/F1 + 탐지 지연.

점(point) 단위가 아니라 '이벤트' 단위로 센다. 운영에서 중요한 것은
"장애 하나를 잡았는가, 얼마나 빨리, 헛알람은 몇 번 울렸나" 이다.
- 탐지(TP 이벤트) : 이벤트 구간 [start, end + tolerance] 안에 탐지가 하나라도 있으면
- 헛알람(FP)       : 어떤 이벤트에도 속하지 않는 탐지들을 연속 구간으로 묶은 '알람 묶음' 수
"""

from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel

from aiops.analytics.anomaly.base import AnomalyDetector
from aiops.analytics.anomaly.synthetic import LabeledSeries


class KindScore(BaseModel):
    kind: str
    events: int
    detected: int
    false_alarms: int
    precision: float
    recall: float
    f1: float
    mean_delay: float | None


class DetectorReport(BaseModel):
    name: str
    overall_f1: float
    by_kind: list[KindScore]

    def kind(self, k: str) -> KindScore:
        return next(s for s in self.by_kind if s.kind == k)


def _clusters(indices: list[int], gap: int = 3) -> int:
    count, prev = 0, None
    for i in sorted(indices):
        if prev is None or i - prev > gap:
            count += 1
        prev = i
    return count


def score_series(det_indices: list[int], s: LabeledSeries, tol: int = 10):
    detected, delays, used = 0, [], set()
    for start, end in s.events:
        hits = [i for i in det_indices if start <= i <= end + tol]
        if hits:
            detected += 1
            delays.append(min(hits) - start)
            used.update(hits)
    false = _clusters([i for i in det_indices if i not in used])
    return detected, false, delays


def evaluate_detector(
    name: str, factory: Callable[[LabeledSeries], AnomalyDetector], data: list[LabeledSeries]
) -> DetectorReport:
    agg = defaultdict(lambda: {"events": 0, "detected": 0, "false": 0, "delays": []})
    for s in data:
        idx = [a.index for a in factory(s).detect(s.values)]
        d, f, delays = score_series(idx, s)
        a = agg[s.kind]
        a["events"] += len(s.events)
        a["detected"] += d
        a["false"] += f
        a["delays"] += delays
    scores, tp_all, fp_all, ev_all = [], 0, 0, 0
    for kind, a in agg.items():
        p = a["detected"] / (a["detected"] + a["false"]) if a["detected"] + a["false"] else 1.0
        r = a["detected"] / a["events"] if a["events"] else 1.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        scores.append(
            KindScore(
                kind=kind,
                events=a["events"],
                detected=a["detected"],
                false_alarms=a["false"],
                precision=round(p, 3),
                recall=round(r, 3),
                f1=round(f1, 3),
                mean_delay=round(sum(a["delays"]) / len(a["delays"]), 1) if a["delays"] else None,
            )
        )
        tp_all += a["detected"]
        fp_all += a["false"]
        ev_all += a["events"]
    p = tp_all / (tp_all + fp_all) if tp_all + fp_all else 1.0
    r = tp_all / ev_all if ev_all else 1.0
    return DetectorReport(
        name=name, overall_f1=round(2 * p * r / (p + r) if p + r else 0, 3), by_kind=scores
    )
