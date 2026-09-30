"""RAG 서비스 (4.2): 인제스트 → 하이브리드 검색 → 근거 기반 답변 생성."""

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from aiops.core.config import Settings
from aiops.llm.base import ChatMessage, LLMProvider
from aiops.prompts.registry import PromptRegistry
from aiops.rag.chunking import chunk_document
from aiops.rag.citations import validate_citations
from aiops.rag.embeddings.base import Embedder, HashingEmbedder, OpenAICompatEmbedder
from aiops.rag.hybrid import HybridRetriever, Reranker
from aiops.rag.models import Document, ScoredChunk
from aiops.rag.vectorstores.base import InMemoryVectorStore, VectorStore

logger = logging.getLogger(__name__)


def load_markdown_dir(path: Path) -> list[Document]:
    """디렉터리의 *.md 를 Document 로 로드 (파일 I/O 는 동기 → to_thread 로 호출)."""
    if not path.exists():
        logger.warning("knowledge dir %s not found", path)
        return []
    return [
        Document(
            id=p.stem,
            text=p.read_text(encoding="utf-8"),
            metadata={"source": str(p), "title": p.stem, "doc_type": "runbook"},
        )
        for p in sorted(path.glob("**/*.md"))
    ]


class IngestReport(BaseModel):
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    chunks: int = 0


def content_hash(doc: Document) -> str:
    payload = json.dumps({"t": doc.text, "m": doc.metadata}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class RAGService:
    def __init__(self, retriever: HybridRetriever, llm: LLMProvider, prompts: PromptRegistry):
        self.retriever = retriever
        self.llm = llm
        self.prompts = prompts
        self._hashes: dict[str, str] = {}  # doc_id -> content hash
        self._lock = asyncio.Lock()  # 같은 문서의 동시 교체로 청크가 중복되지 않게

    async def ingest(self, docs: list[Document]) -> IngestReport:
        """증분 인제스트 (M1-05): 내용이 같으면 skip, 바뀌었으면 기존 청크를 지우고 교체."""
        report = IngestReport()
        async with self._lock:
            for doc in docs:
                h = content_hash(doc)
                prev = self._hashes.get(doc.id)
                if prev == h:
                    report.skipped.append(doc.id)
                    continue
                if prev is not None:
                    await self.retriever.delete_doc(doc.id)
                    report.updated.append(doc.id)
                else:
                    report.added.append(doc.id)
                chunks = chunk_document(doc)
                await self.retriever.index(chunks)
                self._hashes[doc.id] = h
                report.chunks += len(chunks)
        return report

    async def delete(self, doc_id: str) -> bool:
        async with self._lock:
            if doc_id not in self._hashes:
                return False
            await self.retriever.delete_doc(doc_id)
            del self._hashes[doc_id]
            return True

    def documents(self) -> list[str]:
        return sorted(self._hashes)

    async def ingest_directory(self, path: Path) -> IngestReport:
        docs = await asyncio.to_thread(load_markdown_dir, path)
        return await self.ingest(docs)

    async def search(
        self, query: str, k: int = 5, filters: dict | None = None
    ) -> list[ScoredChunk]:
        return await self.retriever.retrieve(query, k=k, filters=filters)

    async def answer(self, question: str, k: int = 5) -> dict:
        hits = await self.search(question, k=k)
        context = "\n\n".join(
            f"[{i + 1}] ({h.chunk.doc_id}) {h.chunk.text}" for i, h in enumerate(hits)
        )
        prompt = self.prompts.render("rag_answer", question=question, context=context)
        resp = await self.llm.chat(
            [ChatMessage.system(prompt.system), ChatMessage.user(prompt.user)]
        )
        numbered = [h.chunk.doc_id for h in hits]
        report = validate_citations(resp.content or "", set(numbered), numbered)
        return {
            "answer": resp.content,
            "citations": [{"doc_id": h.chunk.doc_id, "chunk_id": h.chunk.id} for h in hits],
            "citation_report": report.model_dump(),
        }


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "openai":
        return OpenAICompatEmbedder(
            settings.openai_base_url,
            settings.embedding_model,
            settings.embedding_dim,
            api_key=settings.openai_api_key,
        )
    if settings.embedding_provider == "local":
        return OpenAICompatEmbedder(
            settings.local_llm_base_url, settings.embedding_model, settings.embedding_dim
        )
    return HashingEmbedder(settings.embedding_dim)


def build_reranker(settings: Settings, llm: LLMProvider) -> Reranker | None:
    if settings.reranker == "http":
        from aiops.rag.rerankers import HTTPReranker

        return HTTPReranker(
            settings.reranker_url, settings.reranker_model, api_style=settings.reranker_api_style
        )
    if settings.reranker == "llm":
        from aiops.rag.rerankers import LLMReranker

        return LLMReranker(llm)
    return None


def build_vector_store(settings: Settings) -> VectorStore:
    if settings.vector_backend == "qdrant":
        from aiops.rag.vectorstores.qdrant import QdrantVectorStore

        return QdrantVectorStore(settings.qdrant_url, "ops_knowledge", settings.embedding_dim)
    return InMemoryVectorStore()
