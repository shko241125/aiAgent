"""RAG / Knowledge Base API (4.2, 2.6)."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from aiops.api.deps import PlatformDep
from aiops.rag.models import Document

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


class IngestRequest(BaseModel):
    documents: list[Document]


class SearchRequest(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=50)
    filters: dict[str, Any] | None = None


@router.post("/ingest")
async def ingest(req: IngestRequest, p: PlatformDep) -> dict:
    return {"chunks": await p.rag.ingest(req.documents)}


@router.post("/search")
async def search(req: SearchRequest, p: PlatformDep) -> list[dict]:
    hits = await p.rag.search(req.query, k=req.k, filters=req.filters)
    return [
        {
            "doc_id": h.chunk.doc_id,
            "chunk_id": h.chunk.id,
            "score": h.score,
            "sources": h.sources,
            "text": h.chunk.text,
        }
        for h in hits
    ]


@router.post("/answer")
async def answer(req: SearchRequest, p: PlatformDep) -> dict:
    return await p.rag.answer(req.query, k=req.k)
