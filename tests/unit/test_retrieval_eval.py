from pathlib import Path

from aiops.rag.chunking import chunk_document
from aiops.rag.embeddings.base import HashingEmbedder
from aiops.rag.evaluation import evaluate, load_queries, ndcg_at_k, recall_at_k, reciprocal_rank
from aiops.rag.hybrid import HybridRetriever
from aiops.rag.service import load_markdown_dir
from aiops.rag.vectorstores.base import InMemoryVectorStore

ROOT = Path(__file__).resolve().parents[2]


def test_metric_definitions():
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"b", "z"}, 2) == 0.5
    assert reciprocal_rank(ranked, {"c"}) == 1 / 3
    assert ndcg_at_k(["x", "a"], {"a"}, 2) < ndcg_at_k(["a", "x"], {"a"}, 2) == 1.0


async def test_hybrid_retrieval_quality_floor():
    """회귀 방지선: 검색·청킹·토크나이저 변경이 품질을 떨어뜨리면 실패한다 (M1-06 기준선)."""
    chunks = [c for d in load_markdown_dir(ROOT / "data/knowledge") for c in chunk_document(d)]
    retriever = HybridRetriever(HashingEmbedder(384), InMemoryVectorStore())
    await retriever.index(chunks)
    queries = load_queries(ROOT / "data/eval/retrieval.jsonl")
    report = await evaluate("hybrid", retriever.retrieve, queries, k=5)
    lexical = report.by_kind()["lexical"]
    assert lexical >= 0.95, report.misses()
    assert report.mrr >= 0.8
