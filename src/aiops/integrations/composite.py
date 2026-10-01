"""소스 조합 (M2-01).

메트릭·로그·이벤트·토폴로지의 출처가 달라도 에이전트는 하나의 OpsSource 만 본다.
"""

import json
from datetime import datetime
from pathlib import Path

from aiops.domain.models import MetricSeries, OpsEvent
from aiops.integrations.base import (
    EventSource,
    LogSource,
    MetricsSource,
    OpsSource,
    TopologySource,
)


class FileTopologySource(TopologySource):
    """`{"service": {"downstream": [...]}}` JSON — upstream 은 역방향으로 자동 계산.

    TODO(2.1): Kubernetes Service/Istio 텔레메트리 기반 자동 토폴로지 발견.
    """

    def __init__(self, path: Path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
        self.downstream = {s: list(v.get("downstream", [])) for s, v in raw.items()}
        self.upstream: dict[str, list[str]] = {}
        for svc, deps in self.downstream.items():
            for d in deps:
                self.upstream.setdefault(d, []).append(svc)

    async def dependencies(self, service: str) -> dict[str, list[str]]:
        return {
            "upstream": sorted(self.upstream.get(service, [])),
            "downstream": self.downstream.get(service, []),
        }


class NullEventSource(EventSource):
    async def list_events(self, start, end, service=None) -> list[OpsEvent]:
        return []


class CompositeOpsSource(OpsSource):
    def __init__(
        self, metrics: MetricsSource, logs: LogSource, events: EventSource, topology: TopologySource
    ) -> None:
        self.metrics, self.logs, self.events, self.topology = metrics, logs, events, topology

    async def query_range(
        self, service: str, metric: str, start: datetime, end: datetime, step_s: int = 60
    ) -> MetricSeries:
        return await self.metrics.query_range(service, metric, start, end, step_s)

    async def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 100
    ) -> list[dict]:
        return await self.logs.search(service, query, start, end, limit)

    async def list_events(
        self, start: datetime, end: datetime, service: str | None = None
    ) -> list[OpsEvent]:
        return await self.events.list_events(start, end, service)

    async def dependencies(self, service: str) -> dict[str, list[str]]:
        return await self.topology.dependencies(service)

    async def aclose(self) -> None:
        for part in (self.metrics, self.logs, self.events, self.topology):
            if hasattr(part, "aclose"):
                await part.aclose()
