"""M2-01 실데이터 어댑터 — Prometheus/Loki HTTP 계약 + 조합 소스로 RCA 동작."""

from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from aiops.analytics.rca import RCAAnalyzer
from aiops.domain.models import utcnow
from aiops.integrations.composite import CompositeOpsSource, FileTopologySource, NullEventSource
from aiops.integrations.loki import LokiLogSource
from aiops.integrations.prometheus import PrometheusError, PrometheusMetricsSource

ROOT = Path(__file__).resolve().parents[2]


def _prom_handler(seen, spike_service="order-service", spike_metric="error_rate"):
    def handler(req: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(req.url)).query)
        seen.append(q)
        start, end = float(q["start"][0]), float(q["end"][0])
        n = int((end - start) // 60)
        query = q["query"][0]
        is_spike = f'service="{spike_service}"' in query and (
            "5.." in query if spike_metric == "error_rate" else False
        )
        values = []
        for i in range(n):
            v = 0.5 + 0.02 * (i % 3)
            if is_spike and i >= n - 10:
                v *= 20
            values.append([start + i * 60, str(v)])
        values.append([end, "NaN"])  # Prometheus 는 결측을 NaN 문자열로 준다
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"metric": {}, "values": values}]},
            },
        )

    return handler


async def test_prometheus_contract_and_template():
    seen = []
    client = httpx.AsyncClient(
        base_url="http://prom", transport=httpx.MockTransport(_prom_handler(seen))
    )
    src = PrometheusMetricsSource(
        "http://prom",
        labels={"namespace": "prod"},
        queries={"custom": 'up{job="$service",ns="$namespace"}'},
        http_client=client,
    )
    assert src.promql("api", "custom") == 'up{job="api",ns="prod"}'
    end = utcnow()
    series = await src.query_range("order-service", "error_rate", end - timedelta(hours=1), end)
    assert len(series.points) == 60  # NaN 제외
    assert seen[0]["step"] == ["60s"] and 'status=~"5.."' in seen[0]["query"][0]


async def test_prometheus_error_surfaces():
    client = httpx.AsyncClient(
        base_url="http://p",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"status": "error", "error": "parse error"})
        ),
    )
    src = PrometheusMetricsSource("http://p", http_client=client)
    end = utcnow()
    with pytest.raises(PrometheusError, match="parse error"):
        await src.query_range("a", "cpu_usage", end - timedelta(minutes=5), end)


def _loki_handler(seen, lines):
    def handler(req: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(req.url)).query)
        seen.append(q)
        now_ns = int(utcnow().timestamp() * 1e9)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"app": "x", "level": "error"},
                            "values": [[str(now_ns - i), ln] for i, ln in enumerate(lines)],
                        }
                    ],
                },
            },
        )

    return handler


async def test_loki_contract():
    seen = []
    client = httpx.AsyncClient(
        base_url="http://loki",
        transport=httpx.MockTransport(_loki_handler(seen, ["HikariPool timeout", "retry"])),
    )
    src = LokiLogSource("http://loki", http_client=client)
    assert src.logql("order-service", 'say "hi"') == '{app="order-service"} |~ "(?i)say \\"hi\\""'
    end = utcnow()
    rows = await src.search("order-service", "error", end - timedelta(minutes=30), end)
    assert [r["message"] for r in rows] == ["HikariPool timeout", "retry"]
    assert int(seen[0]["start"][0]) > 1e18  # 나노초


async def test_file_topology(tmp_path):
    p = tmp_path / "t.json"
    p.write_text('{"a": {"downstream": ["b", "c"]}, "b": {"downstream": ["c"]}}')
    topo = FileTopologySource(p)
    assert await topo.dependencies("c") == {"upstream": ["a", "b"], "downstream": []}


async def test_rca_runs_on_live_composite_source():
    """시뮬레이터 없이 Prometheus+Loki 계약만으로 RCA 근거 수집·랭킹이 동작한다."""
    prom = PrometheusMetricsSource(
        "http://prom",
        http_client=httpx.AsyncClient(
            base_url="http://prom", transport=httpx.MockTransport(_prom_handler([]))
        ),
    )
    loki = LokiLogSource(
        "http://loki",
        http_client=httpx.AsyncClient(
            base_url="http://loki",
            transport=httpx.MockTransport(
                _loki_handler([], ["HikariPool-1 - Connection is not available"])
            ),
        ),
    )
    source = CompositeOpsSource(
        prom, loki, NullEventSource(), FileTopologySource(ROOT / "data/topology.json")
    )
    top = (await RCAAnalyzer(source).analyze("order-service"))[0]
    assert top.service == "order-service" and top.kind == "error_signature"
    assert "커넥션 풀" in top.cause
