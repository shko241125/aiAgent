"""RAG 서비스 (4.2): 인제스트 → 하이브리드 검색 → 근거 기반 답변 생성."""

import asyncio
import logging
from pathlib import Path

from aiops.core.config import Settings
from aiops.llm.base import ChatMessage, LLMProvider
from aiops.prompts.registry import PromptRegistry
from aiops.rag.chunking import chunk_document
from aiops.rag.embeddings.base import Embedder, HashingEmbedder, OpenAICompatEmbedder
from aiops.rag.hybrid import HybridRetriever
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


class RAGService:
    def __init__(self, retriever: HybridRetriever, llm: LLMProvider, prompts: PromptRegistry):
        self.retriever = retriever
        self.llm = llm
        self.prompts = prompts

    async def ingest(self, docs: list[Document]) -> int:
        chunks = [c for d in docs for c in chunk_document(d)]
        await self.retriever.index(chunks)
        return len(chunks)

    async def ingest_directory(self, path: Path) -> int:
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
        return {
            "answer": resp.content,
            "citations": [{"doc_id": h.chunk.doc_id, "chunk_id": h.chunk.id} for h in hits],
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


def build_vector_store(settings: Settings) -> VectorStore:
    if settings.vector_backend == "qdrant":
        from aiops.rag.vectorstores.qdrant import QdrantVectorStore

        return QdrantVectorStore(settings.qdrant_url, "ops_knowledge", settings.embedding_dim)
    return InMemoryVectorStore()
