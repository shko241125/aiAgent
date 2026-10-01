"""라벨된 합성 시계열 (M2-03 / 3.1) — 탐지기 평가용.

실 라벨 데이터가 없을 때 장애 '패턴'별로 정답을 아는 데이터를 만든다.
한계: 실제 운영 데이터의 잡음·결측·다중 계절성은 단순화돼 있다 → 실데이터 라벨셋으로 재검증 필요.
"""

import math
import random

from pydantic import BaseModel

KINDS = ["normal", "seasonal", "spike", "level_shift", "drift", "seasonal_spike"]


class LabeledSeries(BaseModel):
    kind: str
    values: list[float]
    events: list[tuple[int, int]]  # 이상 구간 [start, end]
    period: int | None = None


def generate(
    kind: str, n: int = 600, seed: int = 0, base: float = 100.0, period: int = 120
) -> LabeledSeries:
    rng = random.Random(f"{kind}:{seed}")
    seasonal = kind in ("seasonal", "seasonal_spike")
    values, events = [], []
    for i in range(n):
        v = base + rng.gauss(0, 0.03 * base)
        if seasonal:  # 주기 period 의 강한 계절성 (진폭 = base 의 40%)
            v += 0.4 * base * math.sin(2 * math.pi * i / period)
        values.append(v)

    def spike_at(idx: int, mult: float = 1.6) -> None:
        values[idx] += mult * 0.4 * base if seasonal else (mult - 1) * base * 1.25
        events.append((idx, idx))

    if kind in ("spike", "seasonal_spike"):
        for idx in rng.sample(range(n // 3, n - 5), 4):
            spike_at(idx)
    elif kind == "level_shift":
        t = rng.randint(n // 2, n - 60)
        for i in range(t, n):
            values[i] += 0.5 * base
        events.append((t, t + 20))
    elif kind == "drift":
        t = rng.randint(n // 2, n - 150)
        for i in range(t, n):
            values[i] += 0.8 * base * min(1.0, (i - t) / 120)
        events.append((t, t + 120))
    return LabeledSeries(
        kind=kind, values=values, events=sorted(events), period=period if seasonal else None
    )


def dataset(seeds: int = 5, n: int = 600) -> list[LabeledSeries]:
    return [generate(k, n=n, seed=s) for k in KINDS for s in range(seeds)]
