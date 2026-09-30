"""하이브리드 검색 (4.2): BM25(희소) + Vector(밀집) → RRF 융합 → (선택) Reranker.

RRF(Reciprocal Rank Fusion): score(d) = Σ_r 1 / (k + rank_r(d))
서로 스케일이 다른 점수(BM25 점수 vs 코사인 유사도)를 정규화 없이 '순위'만으로 합친다.
"""

from abc import ABC, abstractmethod
from typing import Any

from aiops.rag.bm25 import BM25Index
from aiops.rag.embeddings.base import Embedder
from aiops.rag.models import Chunk, ScoredChunk
from aiops.rag.vectorstores.base import VectorStore


class Reranker(ABC):
    """TODO(4.2): Cross-encoder(bge-reranker-v2-m3 등) 또는 LLM 기반 재정렬 구현."""

    @abstractmethod
    async def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]: ...


def reciprocal_rank_fusion(
    result_lists: dict[str, list[ScoredChunk]], k: int = 60, weights: dict[str, float] | None = None
) -> list[ScoredChunk]:
    fused: dict[str, ScoredChunk] = {}
    for name, results in result_lists.items():
        w = (weights or {}).get(name, 1.0)
        for rank, sc in enumerate(results, start=1):
            item = fused.setdefault(sc.chunk.id, ScoredChunk(chunk=sc.chunk, score=0.0))
            item.score += w / (k + rank)
            item.sources[f"{name}_rank"] = rank
    return sorted(fused.values(), key=lambda s: s.score, reverse=True)


class HybridRetriever:
    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        bm25: BM25Index | None = None,
        reranker: Reranker | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.bm25 = bm25 or BM25Index()
        self.reranker = reranker
        self.weights = weights or {"bm25": 1.0, "vector": 1.0}

    async def index(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = await self.embedder.embed([c.text for c in chunks])
        await self.vector_store.upsert(chunks, vectors)
        self.bm25.add(chunks)

    async def delete_doc(self, doc_id: str) -> None:
        await self.vector_store.delete_doc(doc_id)
        self.bm25.remove_doc(doc_id)

    async def retrieve(
        self, query: str, k: int = 5, candidates: int = 20, filters: dict[str, Any] | None = None
    ) -> list[ScoredChunk]:
        [qvec] = await self.embedder.embed([query])
        dense = await self.vector_store.search(qvec, k=candidates, filters=filters)
        sparse = self.bm25.search(query, k=candidates)
        if filters:
            sparse = [
                s
                for s in sparse
                if all(s.chunk.metadata.get(key) == v for key, v in filters.items())
            ]
        fused = reciprocal_rank_fusion({"bm25": sparse, "vector": dense}, weights=self.weights)
        if self.reranker:
            fused = await self.reranker.rerank(query, fused[:candidates])
        return fused[:k]
