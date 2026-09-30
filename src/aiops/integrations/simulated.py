"""합성(synthetic) 운영 데이터 소스 — 외부 시스템 없이 전체 흐름을 개발·데모하기 위함.

`inject_incident()` 로 특정 서비스/메트릭에 이상 패턴을 주입할 수 있다.
"""

import math
import random
from datetime import datetime, timedelta

from aiops.domain.models import MetricPoint, MetricSeries, OpsEvent, Severity
from aiops.integrations.base import EventSource, LogSource, MetricsSource, TopologySource

_TOPOLOGY = {
    "api-gateway": {"upstream": [], "downstream": ["order-service", "user-service"]},
    "order-service": {"upstream": ["api-gateway"], "downstream": ["payment-service", "order-db"]},
    "payment-service": {"upstream": ["order-service"], "downstream": ["payment-db"]},
    "user-service": {"upstream": ["api-gateway"], "downstream": ["user-db"]},
}

_BASELINE = {"cpu_usage": 35.0, "memory_usage": 55.0, "latency_p95_ms": 120.0, "error_rate": 0.5}


class SimulatedOpsSource(MetricsSource, LogSource, EventSource, TopologySource):
    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._incidents: dict[tuple[str, str], float] = {}

    def inject_incident(self, service: str, metric: str, magnitude: float = 3.0) -> None:
        """마지막 15% 구간에 baseline * magnitude 수준의 급증을 주입."""
        self._incidents[(service, metric)] = magnitude

    async def query_range(
        self, service: str, metric: str, start: datetime, end: datetime, step_s: int = 60
    ) -> MetricSeries:
        base = _BASELINE.get(metric, 10.0)
        n = max(2, int((end - start).total_seconds() // step_s))
        spike_from = int(n * 0.85)
        magnitude = self._incidents.get((service, metric))
        points = []
        for i in range(n):
            seasonal = 0.1 * base * math.sin(2 * math.pi * i / 60)
            value = base + seasonal + self._rng.gauss(0, 0.05 * base)
            if magnitude and i >= spike_from:
                value *= magnitude
            points.append(MetricPoint(timestamp=start + timedelta(seconds=i * step_s), value=value))
        return MetricSeries(service=service, metric=metric, points=points)

    async def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 100
    ) -> list[dict]:
        has_incident = any(s == service for s, _ in self._incidents)
        if not has_incident:
            return []
        return [
            {
                "ts": end.isoformat(),
                "level": "ERROR",
                "service": service,
                "message": "HikariPool-1 - Connection is not available, request timed out",
            }
        ][:limit]

    async def list_events(
        self, start: datetime, end: datetime, service: str | None = None
    ) -> list[OpsEvent]:
        events = [
            OpsEvent(
                timestamp=end - timedelta(minutes=20),
                source="cicd",
                service="order-service",
                type="deploy",
                message="order-service v2.3.1 deployed",
            )
        ]
        for (svc, metric), _ in self._incidents.items():
            events.append(
                OpsEvent(
                    timestamp=end - timedelta(minutes=5),
                    source="prometheus",
                    service=svc,
                    type=f"alert.{metric}_high",
                    severity=Severity.MAJOR,
                    message=f"{metric} above threshold",
                )
            )
        return [e for e in events if service is None or e.service == service]

    async def dependencies(self, service: str) -> dict[str, list[str]]:
        return _TOPOLOGY.get(service, {"upstream": [], "downstream": []})
