from aiops.rag.bm25 import BM25Index
from aiops.rag.chunking import chunk_document
from aiops.rag.embeddings.base import HashingEmbedder
from aiops.rag.hybrid import HybridRetriever, reciprocal_rank_fusion
from aiops.rag.models import Chunk, Document, ScoredChunk
from aiops.rag.tokenizer import tokenize
from aiops.rag.vectorstores.base import InMemoryVectorStore

DOCS = [
    Document(id="db", text="# DB\n\n## 증상\nHikariPool 커넥션 풀 고갈로 장애가 발생했다."),
    Document(id="cpu", text="# CPU\n\n## 증상\nCPU 사용률 급증 시 scale out 한다."),
]


def test_korean_tokenizer_bigrams():
    toks = tokenize("장애가 발생 order-service")
    assert "장애" in toks and "order" in toks and "service" in toks


def test_chunking_keeps_heading_path():
    chunks = chunk_document(DOCS[0])
    assert chunks[0].text.startswith("[DB > 증상]")
    assert chunks[0].metadata["section"] == "DB > 증상"


def test_bm25_exact_term():
    idx = BM25Index()
    for d in DOCS:
        idx.add(chunk_document(d))
    hits = idx.search("HikariPool 에러")
    assert hits[0].chunk.doc_id == "db"


def test_rrf_prefers_items_ranked_by_both():
    a, b, c = (Chunk(id=i, doc_id=i, text=i) for i in "abc")
    fused = reciprocal_rank_fusion(
        {
            "bm25": [ScoredChunk(chunk=a, score=9), ScoredChunk(chunk=b, score=5)],
            "vector": [ScoredChunk(chunk=b, score=0.9), ScoredChunk(chunk=c, score=0.8)],
        }
    )
    assert fused[0].chunk.id == "b"


async def test_hybrid_retriever_end_to_end():
    r = HybridRetriever(HashingEmbedder(128), InMemoryVectorStore())
    await r.index([c for d in DOCS for c in chunk_document(d)])
    hits = await r.retrieve("커넥션 풀 장애", k=1)
    assert hits[0].chunk.doc_id == "db"
    assert {"bm25_rank", "vector_rank"} & set(hits[0].sources)
