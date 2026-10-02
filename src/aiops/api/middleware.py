"""요청 ID 부여 + 처리시간 측정 + Prometheus 메트릭 (4.6 관측성, M4-03).

TODO(4.6): OpenTelemetry 트레이싱, rate limiting.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from aiops.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS, route_class

logger = logging.getLogger("aiops.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._observe(request, 500, time.perf_counter() - start)
            raise
        elapsed = time.perf_counter() - start
        elapsed_ms = elapsed * 1000
        self._observe(request, response.status_code, elapsed)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"
        logger.info(
            "%s %s %d %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            extra={"request_id": request_id},
        )
        return response

    @staticmethod
    def _observe(request: Request, code: int, seconds: float) -> None:
        # 실제 URL 이 아니라 라우트 템플릿(/incidents/{incident_id}) — 라벨 폭발 방지
        r = request.scope.get("route")
        route = getattr(r, "path", None) or "unmatched"
        if route == "/metrics":
            return  # 스크레이프 자체는 SLO 에서 뺀다
        cls = route_class(route)
        HTTP_REQUESTS.labels(request.method, route, cls, str(code)).inc()
        HTTP_LATENCY.labels(request.method, route, cls).observe(seconds)
