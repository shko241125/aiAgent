"""임베딩 모델 인터페이스 (4.2)."""

import hashlib
from abc import ABC, abstractmethod

import httpx
import numpy as np

from aiops.rag.tokenizer import tokenize


class Embedder(ABC):
    dim: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder(Embedder):
    """Feature hashing 기반 결정적 임베딩 — 테스트/오프라인용. 의미 유사도는 거의 없다."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = np.zeros(self.dim)
            for tok in tokenize(text):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 1) & 1 else -1.0
            norm = np.linalg.norm(v) or 1.0
            out.append((v / norm).tolist())
        return out


class OpenAICompatEmbedder(Embedder):
    """OpenAI `/embeddings` 호환 API — OpenAI, 또는 구축형(TEI/vLLM/Ollama 의 bge-m3 등)."""

    def __init__(
        self, base_url: str, model: str, dim: int, api_key: str | None = None, timeout: float = 30
    ) -> None:
        self.model, self.dim = model, dim
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post("/embeddings", json={"model": self.model, "input": texts})
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
