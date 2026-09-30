"""Reranker (M1-04 / 4.2) — 1차 검색 후보를 질의와 함께 다시 읽어 순서를 정밀화한다.

- HTTPReranker : cross-encoder 서빙(HF TEI `/rerank`, Jina/Cohere 스타일 `/v1/rerank`) 호출
- LLMReranker  : LLM 에 후보 목록을 주고 관련도 점수를 JSON 으로 받는 listwise 재정렬
                 (cross-encoder 서빙이 없을 때)
실패 시에는 항상 원래 순서를 돌려준다 — 재정렬은 품질 향상 수단이지 가용성 위험이 되어선 안 된다.
"""

import logging
from typing import Literal

import httpx

from aiops.llm.base import ChatMessage, LLMProvider
from aiops.rag.hybrid import Reranker
from aiops.rag.models import ScoredChunk

logger = logging.getLogger(__name__)


def _apply(candidates: list[ScoredChunk], scores: dict[int, float], name: str) -> list[ScoredChunk]:
    out = []
    for i, c in enumerate(candidates):
        item = c.model_copy(deep=True)
        item.sources[f"{name}_score"] = scores.get(i, float("-inf"))
        item.sources["first_stage_rank"] = i + 1
        out.append(item)
    out.sort(key=lambda c: c.sources[f"{name}_score"], reverse=True)
    for c in out:
        c.score = c.sources[f"{name}_score"]
    return out


class HTTPReranker(Reranker):
    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_style: Literal["tei", "jina"] = "tei",
        api_key: str | None = None,
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model, self.api_style = model, api_style
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout
        )

    async def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        if not candidates:
            return candidates
        texts = [c.chunk.text for c in candidates]
        try:
            if self.api_style == "tei":
                resp = await self._client.post("/rerank", json={"query": query, "texts": texts})
                resp.raise_for_status()
                scores = {r["index"]: float(r["score"]) for r in resp.json()}
            else:
                resp = await self._client.post(
                    "/v1/rerank", json={"model": self.model, "query": query, "documents": texts}
                )
                resp.raise_for_status()
                scores = {r["index"]: float(r["relevance_score"]) for r in resp.json()["results"]}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("rerank failed, keep first-stage order: %s", exc)
            return candidates
        return _apply(candidates, scores, "rerank")


class LLMReranker(Reranker):
    def __init__(self, llm: LLMProvider, max_candidates: int = 10, max_chars: int = 500) -> None:
        self.llm = llm
        self.max_candidates = max_candidates
        self.max_chars = max_chars

    async def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        from aiops.agents.base import extract_json

        head, tail = candidates[: self.max_candidates], candidates[self.max_candidates :]
        if not head:
            return candidates
        listing = "\n\n".join(f"[{i}] {c.chunk.text[: self.max_chars]}" for i, c in enumerate(head))
        prompt = (
            "질의와 각 문서의 관련도를 0~10 점으로 매기세요. "
            "질의에 직접 답하는 문서가 높은 점수입니다.\n"
            'JSON 만 출력: {"scores": [{"id": 0, "score": 7}, ...]}\n\n'
            f"[질의]\n{query}\n\n[문서]\n{listing}"
        )
        try:
            resp = await self.llm.chat([ChatMessage.user(prompt)], temperature=0.0)
            data = extract_json(resp.content)
            scores = {int(s["id"]): float(s["score"]) for s in data.get("scores", [])}
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM rerank failed: %s", exc)
            return candidates
        if not scores:
            return candidates
        return _apply(head, scores, "llm_rerank") + tail
