"""설정으로 운영 데이터 소스를 조립한다 (M2-01)."""

from aiops.core.config import Settings
from aiops.integrations.base import EventSource, OpsSource
from aiops.integrations.composite import CompositeOpsSource, FileTopologySource, NullEventSource
from aiops.integrations.loki import LokiLogSource
from aiops.integrations.prometheus import PrometheusMetricsSource
from aiops.integrations.simulated import SimulatedOpsSource


def build_ops_source(settings: Settings, events: EventSource | None = None) -> OpsSource:
    if settings.ops_source == "simulated":
        return SimulatedOpsSource()
    return CompositeOpsSource(
        metrics=PrometheusMetricsSource(
            settings.prometheus_url,
            queries=PrometheusMetricsSource.load_queries(settings.prometheus_queries_file),
            labels=settings.prometheus_label_map,
            bearer_token=settings.prometheus_token,
        ),
        logs=LokiLogSource(
            settings.loki_url, selector=settings.loki_selector, tenant=settings.loki_tenant
        ),
        events=events or NullEventSource(),
        topology=FileTopologySource(settings.topology_file),
    )
