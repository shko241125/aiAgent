"""Prometheus 메트릭 (M4-03 / 4.6).

메트릭 객체는 모듈 수준에 한 번만 만든다 (prometheus_client 관용 패턴). 레지스트리는 기본 전역
레지스트리 대신 전용 `REGISTRY` — 라이브러리 기본 수집기(프로세스 등)는 명시적으로 등록한다.

라벨 원칙: 값의 종류가 유한한 것만 (경로는 실제 URL 이 아니라 **라우트 템플릿**).
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    gc_collector,
    generate_latest,
    platform_collector,
    process_collector,
)

REGISTRY = CollectorRegistry()
process_collector.ProcessCollector(registry=REGISTRY)
platform_collector.PlatformCollector(registry=REGISTRY)
gc_collector.GCCollector(registry=REGISTRY)

# 지연 SLO 임계치(slo.py)는 반드시 버킷 경계여야 한다 — 'X초 이내 비율' 을 버킷으로 계산하기 때문
LATENCY_BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

# LLM·에이전트를 거치는 느린 경로 — 대화형 API 와 지연 기대치가 다르므로 SLO 를 분리한다
LLM_ROUTE_PREFIXES = ("/api/v1/orchestrations", "/api/v1/agents/{name}/run", "/api/v1/rag/answer")


def route_class(route: str) -> str:
    return "llm" if route.startswith(LLM_ROUTE_PREFIXES) else "interactive"


HTTP_REQUESTS = Counter(
    "aiops_http_requests_total",
    "HTTP 요청 수",
    ["method", "route", "class", "code"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "aiops_http_request_duration_seconds",
    "HTTP 요청 처리 시간",
    ["method", "route", "class"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
LLM_REQUESTS = Counter(
    "aiops_llm_requests_total",
    "LLM 호출 수 (fallback 포함 시도 단위)",
    ["provider", "agent", "outcome"],
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "aiops_llm_request_duration_seconds",
    "LLM 호출 지연",
    ["provider"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "aiops_llm_tokens_total", "LLM 토큰", ["provider", "direction"], registry=REGISTRY
)
WORKFLOW_STEPS = Counter(
    "aiops_workflow_steps_total",
    "워크플로우 단계 종료 수",
    ["workflow", "step", "status"],
    registry=REGISTRY,
)
APPROVAL_DECISIONS = Counter(
    "aiops_approval_decisions_total", "승인 결정 수", ["status"], registry=REGISTRY
)
APPROVAL_LATENCY = Histogram(
    "aiops_approval_decision_seconds",
    "승인 요청~결정 시간",
    buckets=(60, 300, 900, 1800, 3600, 7200),
    registry=REGISTRY,
)
REMEDIATION_ACTIONS = Counter(
    "aiops_remediation_actions_total",
    "조치 실행 결과",
    ["type", "mode", "outcome"],  # mode: dry_run|real, outcome: ok|failed|blocked
    registry=REGISTRY,
)


def observe_llm(provider: str, agent: str | None, ok: bool, seconds: float, usage: dict) -> None:
    LLM_REQUESTS.labels(provider, agent or "-", "ok" if ok else "error").inc()
    LLM_LATENCY.labels(provider).observe(seconds)
    for direction in ("input", "output"):
        if n := usage.get(f"{direction}_tokens"):
            LLM_TOKENS.labels(provider, direction).inc(n)


TERMINAL_STEP = {"succeeded", "failed", "skipped"}


async def observe_step(run, step_id: str) -> None:
    """WorkflowEngine.on_step_update 훅 — 종료 상태로 체크포인트될 때 한 번 센다."""
    status = str(run.steps[step_id].status)
    if status in TERMINAL_STEP:
        WORKFLOW_STEPS.labels(run.workflow, step_id, status).inc()


def exposition() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
