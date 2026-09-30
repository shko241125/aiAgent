"""RAG / Knowledge Base API (4.2, 2.6)."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aiops.api.deps import PlatformDep
from aiops.rag.models import Document
from aiops.rag.service import IngestReport

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


class IngestRequest(BaseModel):
    documents: list[Document]


class SearchRequest(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=50)
    filters: dict[str, Any] | None = None


@router.post("/ingest", response_model=IngestReport)
async def ingest(req: IngestRequest, p: PlatformDep) -> IngestReport:
    """증분 인제스트: 같은 id·같은 내용은 skip, 내용이 바뀌면 교체."""
    return await p.rag.ingest(req.documents)


@router.get("/documents")
async def documents(p: PlatformDep) -> list[str]:
    return p.rag.documents()


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, p: PlatformDep) -> None:
    if not await p.rag.delete(doc_id):
        raise HTTPException(404, f"document not found: {doc_id}")


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
