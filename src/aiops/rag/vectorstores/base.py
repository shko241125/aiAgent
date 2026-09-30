"""Vector Database 인터페이스 (4.2)."""

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from aiops.rag.models import Chunk, ScoredChunk


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    @abstractmethod
    async def search(
        self, vector: list[float], k: int = 10, filters: dict[str, Any] | None = None
    ) -> list[ScoredChunk]: ...

    @abstractmethod
    async def delete_doc(self, doc_id: str) -> None: ...


class InMemoryVectorStore(VectorStore):
    """numpy 코사인 유사도 전수 탐색 (brute force). 수만 건 이하 개발용."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._matrix: np.ndarray | None = None

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        existing = {c.id: i for i, c in enumerate(self._chunks)}
        rows = [] if self._matrix is None else list(self._matrix)
        for c, v in zip(chunks, vectors, strict=True):
            vec = np.asarray(v, dtype=float)
            vec = vec / (np.linalg.norm(vec) or 1.0)
            if c.id in existing:
                rows[existing[c.id]] = vec
                self._chunks[existing[c.id]] = c
            else:
                self._chunks.append(c)
                rows.append(vec)
        self._matrix = np.vstack(rows) if rows else None

    async def delete_doc(self, doc_id: str) -> None:
        keep = [i for i, c in enumerate(self._chunks) if c.doc_id != doc_id]
        self._chunks = [self._chunks[i] for i in keep]
        self._matrix = self._matrix[keep] if self._matrix is not None and keep else None

    async def search(
        self, vector: list[float], k: int = 10, filters: dict[str, Any] | None = None
    ) -> list[ScoredChunk]:
        if self._matrix is None:
            return []
        q = np.asarray(vector, dtype=float)
        q = q / (np.linalg.norm(q) or 1.0)
        sims = self._matrix @ q
        order = np.argsort(-sims)
        out: list[ScoredChunk] = []
        for i in order:
            c = self._chunks[int(i)]
            if filters and any(c.metadata.get(key) != val for key, val in filters.items()):
                continue
            out.append(
                ScoredChunk(chunk=c, score=float(sims[i]), sources={"vector": float(sims[i])})
            )
            if len(out) >= k:
                break
        return out
