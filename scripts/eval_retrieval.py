"""검색 방식별 품질 비교 (M1-06).

    python scripts/eval_retrieval.py            # bm25 / vector / hybrid (+설정 시 rerank)
    python scripts/eval_retrieval.py --misses   # 놓친 질의 상세
    python scripts/eval_retrieval.py --json     # 기계 판독용

임베딩은 AIOPS_EMBEDDING_PROVIDER 를 따른다. 기본(hash)은 의미 유사도가 거의 없으므로
vector 수치는 실 임베딩(bge-m3 등)으로 다시 측정해야 의미가 있다.
"""

import argparse
import asyncio
import json
from pathlib import Path

from aiops.core.config import get_settings
from aiops.llm.router import build_llm_router
from aiops.rag.chunking import chunk_document
from aiops.rag.evaluation import evaluate, load_queries
from aiops.rag.hybrid import HybridRetriever
from aiops.rag.service import (
    build_embedder,
    build_reranker,
    build_vector_store,
    load_markdown_dir,
)

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--dataset", default=str(ROOT / "data/eval/retrieval.jsonl"))
    ap.add_argument("--misses", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    queries = load_queries(Path(args.dataset))
    chunks = [c for d in load_markdown_dir(ROOT / "data/knowledge") for c in chunk_document(d)]
    hybrid = HybridRetriever(build_embedder(settings), build_vector_store(settings))
    await hybrid.index(chunks)

    async def vector_only(q: str, n: int):
        [vec] = await hybrid.embedder.embed([q])
        return await hybrid.vector_store.search(vec, k=n)

    async def bm25_only(q: str, n: int):
        return hybrid.bm25.search(q, k=n)

    modes = {"bm25": bm25_only, "vector": vector_only, "hybrid": hybrid.retrieve}
    reranker = build_reranker(settings, build_llm_router(settings))
    if reranker is not None:
        reranked = HybridRetriever(hybrid.embedder, hybrid.vector_store, hybrid.bm25, reranker)
        modes["hybrid+rerank"] = reranked.retrieve

    reports = [await evaluate(name, fn, queries, k=args.k) for name, fn in modes.items()]
    if args.json:
        out = [{**r.model_dump(exclude={"queries"}), "by_kind": r.by_kind()} for r in reports]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    print(f"질의 {len(queries)}개 · 청크 {len(chunks)}개 · 임베딩={settings.embedding_provider}")
    print(f"{'mode':15s} Recall@{args.k}   MRR     nDCG@{args.k}   Recall(유형별)")
    for r in reports:
        print(
            f"{r.name:15s} {r.recall_at_k:.3f}      {r.mrr:.3f}   {r.ndcg_at_k:.3f}    "
            f"{r.by_kind()}"
        )
    if args.misses:
        for r in reports:
            for q in r.misses():
                print(f"[{r.name}] miss: {q.query} → {q.ranked_docs[:3]}")


if __name__ == "__main__":
    asyncio.run(main())
