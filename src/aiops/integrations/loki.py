"""Loki 로그 어댑터 (M2-01 / 2.1) — `/loki/api/v1/query_range` (LogQL).

선택자(stream selector)는 템플릿(`{app="$service"}`), 검색어는 대소문자 무시 정규식 필터로 붙인다.
"""

import json
from datetime import UTC, datetime
from string import Template

import httpx

from aiops.integrations.base import LogSource


class LokiError(RuntimeError):
    pass


class LokiLogSource(LogSource):
    def __init__(
        self,
        base_url: str,
        *,
        selector: str = '{app="$service"}',
        tenant: str | None = None,
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.selector = selector
        headers = {"X-Scope-OrgID": tenant} if tenant else {}  # 멀티테넌트 Loki
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout
        )

    def logql(self, service: str, query: str) -> str:
        stream = Template(self.selector).safe_substitute(service=service)
        if not query:
            return stream
        return f"{stream} |~ {json.dumps('(?i)' + query)}"  # json.dumps → LogQL 문자열 이스케이프

    async def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 100
    ) -> list[dict]:
        resp = await self._client.get(
            "/loki/api/v1/query_range",
            params={
                "query": self.logql(service, query),
                "start": int(start.timestamp() * 1e9),  # Loki 는 나노초
                "end": int(end.timestamp() * 1e9),
                "limit": limit,
                "direction": "backward",
            },
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400 or body.get("status") != "success":
            raise LokiError(f"{resp.status_code}: {resp.text[:300]}")
        rows = []
        for stream in body["data"]["result"]:
            labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                rows.append(
                    {
                        "ts": datetime.fromtimestamp(int(ts_ns) / 1e9, tz=UTC).isoformat(),
                        "service": service,
                        "level": labels.get("level", ""),
                        "message": line,
                        "labels": labels,
                    }
                )
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return rows[:limit]

    async def aclose(self) -> None:
        await self._client.aclose()
