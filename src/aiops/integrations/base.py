"""운영 데이터 소스 어댑터 인터페이스.

실제 구현 예: Prometheus/VictoriaMetrics(메트릭), Loki/Elasticsearch(로그),
Kubernetes API(토폴로지·이벤트), ArgoCD/Jenkins(변경 이력), ServiceNow/Jira(ITSM).
"""

from abc import ABC, abstractmethod
from datetime import datetime

from aiops.domain.models import MetricSeries, OpsEvent


class MetricsSource(ABC):
    @abstractmethod
    async def query_range(
        self, service: str, metric: str, start: datetime, end: datetime, step_s: int = 60
    ) -> MetricSeries: ...


class LogSource(ABC):
    @abstractmethod
    async def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 100
    ) -> list[dict]: ...


class EventSource(ABC):
    @abstractmethod
    async def list_events(
        self, start: datetime, end: datetime, service: str | None = None
    ) -> list[OpsEvent]: ...


class TopologySource(ABC):
    @abstractmethod
    async def dependencies(self, service: str) -> dict[str, list[str]]:
        """{"upstream": [...], "downstream": [...]}"""
