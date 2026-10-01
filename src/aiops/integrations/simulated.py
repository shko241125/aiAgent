"""합성(synthetic) 운영 데이터 소스 — 외부 시스템 없이 전체 흐름을 개발·데모·평가하기 위함.

- `inject_incident()` : 단일 메트릭 급증 주입 (간단 데모)
- `apply_scenario()`  : 장애 시나리오 — 여러 서비스 메트릭 이상 + 시작 시각 + 변경 이력 + 로그
                        (RCA 평가용)
"""

import math
import random
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from aiops.domain.models import MetricPoint, MetricSeries, OpsEvent, Severity
from aiops.integrations.base import OpsSource

TOPOLOGY = {
    "api-gateway": {"upstream": [], "downstream": ["order-service", "user-service"]},
    "order-service": {
        "upstream": ["api-gateway"],
        "downstream": ["payment-service", "order-db", "redis-cache"],
    },
    "payment-service": {"upstream": ["order-service"], "downstream": ["payment-db"]},
    "user-service": {"upstream": ["api-gateway"], "downstream": ["user-db"]},
}

_BASELINE = {"cpu_usage": 35.0, "memory_usage": 55.0, "latency_p95_ms": 120.0, "error_rate": 0.5}


class Fault(BaseModel):
    service: str
    metric: str
    magnitude: float = 3.0
    onset_min_ago: float = 9.0  # 몇 분 전부터 이상이 시작됐는가
    duration_min: float | None = None  # None = 지금도 진행 중, 값이 있으면 일시적 이상


class ChangeEvent(BaseModel):
    service: str
    minutes_ago: float
    type: str = "deploy"  # deploy | config
    message: str = ""


class AcceptedCause(BaseModel):
    service: str
    kinds: list[str]


class FaultScenario(BaseModel):
    id: str
    description: str = ""
    alert_service: str
    faults: list[Fault] = Field(default_factory=list)
    changes: list[ChangeEvent] = Field(default_factory=list)
    logs: dict[str, list[str]] = Field(default_factory=dict)
    accept: list[AcceptedCause] = Field(default_factory=list)  # 정답으로 인정할 (서비스, 종류)


class SimulatedOpsSource(OpsSource):
    def __init__(self, seed: int = 42) -> None:
        self._seed = seed
        self._faults: list[Fault] = []
        self._changes: list[ChangeEvent] = [
            ChangeEvent(service="order-service", minutes_ago=20, message="v2.3.1 deployed")
        ]
        self._logs: dict[str, list[str]] = {}

    def inject_incident(self, service: str, metric: str, magnitude: float = 3.0) -> None:
        self._faults.append(Fault(service=service, metric=metric, magnitude=magnitude))
        self._logs.setdefault(service, []).append(
            "HikariPool-1 - Connection is not available, request timed out"
        )

    def apply_scenario(self, scenario: FaultScenario) -> None:
        self._faults = list(scenario.faults)
        self._changes = list(scenario.changes)
        self._logs = {k: list(v) for k, v in scenario.logs.items()}

    async def query_range(
        self, service: str, metric: str, start: datetime, end: datetime, step_s: int = 60
    ) -> MetricSeries:
        rng = random.Random(f"{self._seed}:{service}:{metric}")  # 호출 순서와 무관하게 재현 가능
        base = _BASELINE.get(metric, 10.0)
        n = max(2, int((end - start).total_seconds() // step_s))
        faults = [f for f in self._faults if f.service == service and f.metric == metric]
        points = []
        for i in range(n):
            ts = start + timedelta(seconds=i * step_s)
            seasonal = 0.1 * base * math.sin(2 * math.pi * i / 60)
            value = base + seasonal + rng.gauss(0, 0.05 * base)
            for f in faults:
                f_start = end - timedelta(minutes=f.onset_min_ago)
                f_end = f_start + timedelta(minutes=f.duration_min) if f.duration_min else end
                if f_start <= ts <= f_end:
                    value *= f.magnitude
            points.append(MetricPoint(timestamp=ts, value=value))
        return MetricSeries(service=service, metric=metric, points=points)

    async def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 100
    ) -> list[dict]:
        return [
            {"ts": end.isoformat(), "level": "ERROR", "service": service, "message": m}
            for m in self._logs.get(service, [])
        ][:limit]

    async def list_events(
        self, start: datetime, end: datetime, service: str | None = None
    ) -> list[OpsEvent]:
        events = [
            OpsEvent(
                timestamp=end - timedelta(minutes=c.minutes_ago),
                source="cicd",
                service=c.service,
                type=c.type,
                message=c.message or f"{c.service} {c.type}",
            )
            for c in self._changes
        ]
        for f in self._faults:
            events.append(
                OpsEvent(
                    timestamp=end - timedelta(minutes=f.onset_min_ago),
                    source="prometheus",
                    service=f.service,
                    type=f"alert.{f.metric}_high",
                    severity=Severity.MAJOR,
                    message=f"{f.metric} above threshold",
                )
            )
        return [
            e
            for e in events
            if start <= e.timestamp <= end and (service is None or e.service == service)
        ]

    async def dependencies(self, service: str) -> dict[str, list[str]]:
        return TOPOLOGY.get(service, {"upstream": [], "downstream": []})
