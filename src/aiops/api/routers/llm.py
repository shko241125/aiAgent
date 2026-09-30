"""LLM 운영 API (M1-02): 사용량·지연·비용."""

from typing import Literal

from fastapi import APIRouter

from aiops.api.deps import PlatformDep
from aiops.llm.router import LLMRouter
from aiops.llm.usage import UsageGroup

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


@router.get("/usage", response_model=list[UsageGroup])
async def usage(
    p: PlatformDep, group_by: Literal["provider", "model", "agent"] = "provider"
) -> list[UsageGroup]:
    llm = p.llm
    return llm.usage.summary(group_by) if isinstance(llm, LLMRouter) else []


@router.get("/providers")
async def providers(p: PlatformDep) -> dict:
    llm = p.llm
    if not isinstance(llm, LLMRouter):
        return {"default": getattr(llm, "name", "custom"), "providers": []}
    return {"default": llm.default, "fallbacks": llm.fallbacks, "providers": list(llm.providers)}
