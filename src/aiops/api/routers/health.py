from fastapi import APIRouter

from aiops import __version__
from aiops.api.deps import PlatformDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
async def ready(p: PlatformDep) -> dict:
    """의존 구성요소 준비 상태. TODO(4.6): DB ping, Vector DB, LLM 헬스체크 포함."""
    return {
        "status": "ready",
        "llm_default": p.settings.llm_default_provider,
        "agents": [a["name"] for a in p.agents.describe()],
        "tools": p.tools.names(),
        "knowledge_chunks": len(p.rag.retriever.bm25),
    }
