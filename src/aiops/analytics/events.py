"""운영 이벤트 분석 및 패턴 탐지 (3.3).

1) 중복 제거(dedup): 같은 서비스·타입의 반복 알람을 하나로 압축 → 알람 폭주(alert storm) 완화
2) 상관 그룹핑(correlation): 시간 창 + 토폴로지 인접성으로 이벤트를 묶어 하나의 '상황'으로 인식
3) 패턴 마이닝: 자주 함께 발생하는 이벤트 타입 시퀀스 집계 (예: deploy → latency_high)
"""

from collections import Counter
from datetime import timedelta

from pydantic import BaseModel, Field

from aiops.domain.models import OpsEvent


class EventCluster(BaseModel):
    events: list[OpsEvent]
    services: list[str]
    types: list[str]
    start: str
    end: str


class PatternStat(BaseModel):
    pattern: tuple[str, ...]
    count: int
    examples: list[str] = Field(default_factory=list)


def deduplicate(events: list[OpsEvent], window: timedelta = timedelta(minutes=5)) -> list[OpsEvent]:
    last_seen: dict[tuple[str, str], OpsEvent] = {}
    out: list[OpsEvent] = []
    for e in sorted(events, key=lambda e: e.timestamp):
        key = (e.service, e.type)
        prev = last_seen.get(key)
        if prev and e.timestamp - prev.timestamp <= window:
            prev.attributes["dup_count"] = prev.attributes.get("dup_count", 1) + 1
            continue
        last_seen[key] = e
        out.append(e)
    return out


def correlate(
    events: list[OpsEvent],
    window: timedelta = timedelta(minutes=10),
    topology: dict[str, set[str]] | None = None,
) -> list[EventCluster]:
    """시간적으로 가깝고 (topology 가 주어지면) 서로 인접한 서비스의 이벤트를 한 클러스터로 묶는다.

    topology: {서비스: 인접 서비스 집합}
    """
    clusters: list[list[OpsEvent]] = []
    for e in sorted(events, key=lambda e: e.timestamp):
        placed = False
        for c in clusters:
            if e.timestamp - c[-1].timestamp > window:
                continue
            services = {x.service for x in c}
            related = (
                topology is None
                or e.service in services
                or any(
                    e.service in topology.get(s, set()) or s in topology.get(e.service, set())
                    for s in services
                )
            )
            if related:
                c.append(e)
                placed = True
                break
        if not placed:
            clusters.append([e])
    return [
        EventCluster(
            events=c,
            services=sorted({e.service for e in c}),
            types=[e.type for e in c],
            start=c[0].timestamp.isoformat(),
            end=c[-1].timestamp.isoformat(),
        )
        for c in clusters
    ]


def mine_patterns(
    clusters: list[EventCluster], length: int = 2, top_k: int = 10
) -> list[PatternStat]:
    """클러스터 내 이벤트 타입의 연속 n-gram 빈도.

    TODO(3.3): PrefixSpan/SPADE 등 순차 패턴 마이닝, 로그 템플릿 추출(Drain) 연동.
    """
    counter: Counter[tuple[str, ...]] = Counter()
    for c in clusters:
        for i in range(len(c.types) - length + 1):
            counter[tuple(c.types[i : i + length])] += 1
    return [PatternStat(pattern=p, count=n) for p, n in counter.most_common(top_k)]
