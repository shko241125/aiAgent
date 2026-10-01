"""Prometheus 메트릭 어댑터 (M2-01 / 2.1).

`/api/v1/query_range` 는 Prometheus·VictoriaMetrics·Thanos·Mimir 가 공통으로 제공한다.
서비스·메트릭 이름 → PromQL 은 템플릿으로 매핑한다. PromQL 은 `{}` 를 많이 쓰므로
str.format 대신 string.Template(`$service`) 치환을 쓴다.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from string import Template

import httpx

from aiops.domain.models import MetricPoint, MetricSeries
from aiops.integrations.base import MetricsSource

# 일반적인 Kubernetes + Spring/Micrometer 지표 기준 기본 템플릿 — 환경에 맞게 파일로 덮어쓴다
DEFAULT_QUERIES: dict[str, str] = {
    "cpu_usage": 'sum(rate(container_cpu_usage_seconds_total{namespace="$namespace",'
    'pod=~"$service-.*",container!=""}[5m])) / sum(kube_pod_container_resource_limits'
    '{namespace="$namespace",pod=~"$service-.*",resource="cpu"}) * 100',
    "memory_usage": 'sum(container_memory_working_set_bytes{namespace="$namespace",'
    'pod=~"$service-.*",container!=""}) / sum(kube_pod_container_resource_limits'
    '{namespace="$namespace",pod=~"$service-.*",resource="memory"}) * 100',
    "latency_p95_ms": "histogram_quantile(0.95, sum by (le) (rate("
    'http_server_requests_seconds_bucket{service="$service"}[5m]))) * 1000',
    "error_rate": 'sum(rate(http_server_requests_seconds_count{service="$service",'
    'status=~"5.."}[5m])) / sum(rate(http_server_requests_seconds_count'
    '{service="$service"}[5m])) * 100',
}


class PrometheusError(RuntimeError):
    pass


class PrometheusMetricsSource(MetricsSource):
    def __init__(
        self,
        base_url: str,
        *,
        queries: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        bearer_token: str | None = None,
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.queries = {**DEFAULT_QUERIES, **(queries or {})}
        self.labels = labels or {"namespace": "default"}
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout
        )

    @staticmethod
    def load_queries(path: Path | None) -> dict[str, str]:
        if path is None or not Path(path).exists():
            return {}
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def promql(self, service: str, metric: str) -> str:
        if metric not in self.queries:
            raise PrometheusError(f"PromQL 템플릿이 없는 메트릭: {metric}")
        return Template(self.queries[metric]).safe_substitute(service=service, **self.labels)

    async def query_range(
        self, service: str, metric: str, start: datetime, end: datetime, step_s: int = 60
    ) -> MetricSeries:
        resp = await self._client.get(
            "/api/v1/query_range",
            params={
                "query": self.promql(service, metric),
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": f"{step_s}s",
            },
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400 or body.get("status") != "success":
            raise PrometheusError(f"{resp.status_code}: {body.get('error', resp.text[:300])}")
        result = body["data"]["result"]
        values = result[0]["values"] if result else []  # 집계 쿼리 → 단일 시계열
        points = [
            MetricPoint(timestamp=datetime.fromtimestamp(float(ts), tz=start.tzinfo), value=v)
            for ts, raw in values
            if not math.isnan(v := float(raw)) and not math.isinf(v)
        ]
        return MetricSeries(
            service=service, metric=metric, points=points, labels={"source": "prometheus"}
        )

    async def aclose(self) -> None:
        await self._client.aclose()
