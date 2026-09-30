"""M1-04 Reranker · M1-05 증분 인제스트."""

import json

import httpx

from aiops.llm.base import LLMResponse
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.prompts.registry import PromptRegistry
from aiops.rag.embeddings.base import HashingEmbedder
from aiops.rag.hybrid import HybridRetriever
from aiops.rag.models import Chunk, Document, ScoredChunk
from aiops.rag.rerankers import HTTPReranker, LLMReranker
from aiops.rag.service import RAGService
from aiops.rag.vectorstores.base import InMemoryVectorStore


def _cands(*texts):
    return [
        ScoredChunk(chunk=Chunk(id=f"c{i}", doc_id=f"d{i}", text=t), score=1.0)
        for i, t in enumerate(texts)
    ]


async def test_http_reranker_tei_and_jina_styles():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.url.path, json.loads(req.content)))
        if req.url.path == "/rerank":
            return httpx.Response(
                200, json=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}]
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 1, "relevance_score": 0.8},
                ]
            },
        )

    for style in ("tei", "jina"):
        client = httpx.AsyncClient(base_url="http://r", transport=httpx.MockTransport(handler))
        out = await HTTPReranker("http://r", "m", api_style=style, http_client=client).rerank(
            "q", _cands("a", "b")
        )
        assert [c.chunk.id for c in out] == ["c1", "c0"]
        assert out[0].sources["first_stage_rank"] == 2
    assert seen[0] == ("/rerank", {"query": "q", "texts": ["a", "b"]})
    assert seen[1][0] == "/v1/rerank" and seen[1][1]["documents"] == ["a", "b"]


async def test_http_reranker_failure_keeps_order():
    client = httpx.AsyncClient(
        base_url="http://r", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    cands = _cands("a", "b")
    assert await HTTPReranker("http://r", http_client=client).rerank("q", cands) == cands


async def test_llm_reranker():
    llm = FakeLLMProvider(
        [LLMResponse(content='{"scores": [{"id": 0, "score": 1}, {"id": 1, "score": 9}]}')]
    )
    out = await LLMReranker(llm, max_candidates=2).rerank("q", _cands("a", "b", "c"))
    assert [c.chunk.id for c in out] == ["c1", "c0", "c2"]  # 범위 밖(c2)은 뒤에 그대로


async def test_incremental_ingest_skip_update_delete():
    rag = RAGService(
        HybridRetriever(HashingEmbedder(64), InMemoryVectorStore()),
        FakeLLMProvider(),
        PromptRegistry(),
    )
    d1 = Document(id="db", text="# DB\n\nHikariPool 커넥션 고갈")
    d2 = Document(id="cpu", text="# CPU\n\nCPU 급증 scale out")
    r1 = await rag.ingest([d1, d2])
    assert r1.added == ["db", "cpu"] and r1.chunks == 2

    r2 = await rag.ingest([d1, Document(id="cpu", text="# CPU\n\n스로틀링 확인 후 scale out")])
    assert r2.skipped == ["db"] and r2.updated == ["cpu"]
    assert len(rag.retriever.bm25) == 2  # 교체이지 누적이 아니다
    hits = await rag.search("스로틀링")
    assert hits[0].chunk.doc_id == "cpu"

    assert await rag.delete("db") and not await rag.delete("db")
    assert rag.documents() == ["cpu"]
    assert all(h.chunk.doc_id != "db" for h in await rag.search("HikariPool 커넥션"))
