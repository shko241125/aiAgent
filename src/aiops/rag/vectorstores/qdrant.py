"""Qdrant Vector DB 어댑터 — `pip install .[vector]` 필요.

TODO(4.2): 컬렉션 자동 생성 파라미터(HNSW m/ef), payload 인덱스, sparse vector(BM25/SPLADE)를
           Qdrant 내부 하이브리드 검색으로 이관하는 옵션 검토.
"""

from typing import Any

from aiops.rag.models import Chunk, ScoredChunk
from aiops.rag.vectorstores.base import VectorStore


class QdrantVectorStore(VectorStore):
    def __init__(self, url: str, collection: str, dim: int) -> None:
        from qdrant_client import AsyncQdrantClient

        self._client = AsyncQdrantClient(url=url)
        self.collection, self.dim = collection, dim
        self._ready = False

    async def _ensure_collection(self) -> None:
        if self._ready:
            return
        from qdrant_client.models import Distance, VectorParams

        if not await self._client.collection_exists(self.collection):
            await self._client.create_collection(
                self.collection,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )
        self._ready = True

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        import uuid

        from qdrant_client.models import PointStruct

        await self._ensure_collection()
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.id)),
                vector=v,
                payload={"chunk": c.model_dump()},
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        await self._client.upsert(self.collection, points=points)

    async def search(
        self, vector: list[float], k: int = 10, filters: dict[str, Any] | None = None
    ) -> list[ScoredChunk]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await self._ensure_collection()
        qfilter = None
        if filters:
            qfilter = Filter(
                must=[
                    FieldCondition(key=f"chunk.metadata.{key}", match=MatchValue(value=val))
                    for key, val in filters.items()
                ]
            )
        res = await self._client.query_points(
            self.collection, query=vector, limit=k, query_filter=qfilter
        )
        return [
            ScoredChunk(
                chunk=Chunk.model_validate(p.payload["chunk"]),
                score=p.score,
                sources={"vector": p.score},
            )
            for p in res.points
        ]
