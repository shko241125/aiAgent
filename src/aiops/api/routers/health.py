from fastapi import APIRouter, Response
from sqlalchemy import text

from aiops import __version__
from aiops.api.auth import PUBLIC, requires
from aiops.api.deps import PlatformDep
from aiops.llm.router import LLMRouter
from aiops.observability.metrics import exposition

router = APIRouter(tags=["health"])


@router.get("/health")
@requires(PUBLIC)
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/metrics", include_in_schema=False)
@requires(PUBLIC)  # 스크레이퍼용. 외부 노출은 네트워크 정책으로 막는다
async def metrics() -> Response:
    body, content_type = exposition()
    return Response(body, media_type=content_type)


@router.get("/ready")
@requires(PUBLIC)
async def ready(p: PlatformDep, response: Response) -> dict:
    """의존 구성요소 준비 상태 (M4-03): DB 는 실제로 질의하고, 실패하면 503.

    LLM 서킷이 열려 있어도 503 으로 만들지 않는다 — fallback·규칙 경로로 동작은 계속되므로
    준비 상태(트래픽 받기)와 성능 저하(degraded)를 구분한다.
    TODO(4.6): Vector DB(Qdrant) 헬스체크.
    """
    checks: dict[str, str] = {}
    try:
        async with p.sessionmaker() as s:
            await s.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"
    if isinstance(p.llm, LLMRouter):
        for name, breaker in p.llm.breakers.items():
            checks[f"llm:{name}"] = "circuit_open" if breaker.is_open else "ok"
    ok = checks["database"] == "ok"
    degraded = any(v != "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {
        "status": "ready" if not degraded else ("degraded" if ok else "not_ready"),
        "checks": checks,
        "llm_default": p.settings.llm_default_provider,
        "agents": [a["name"] for a in p.agents.describe()],
        "tools": p.tools.names(),
        "knowledge_chunks": len(p.rag.retriever.bm25),
    }
