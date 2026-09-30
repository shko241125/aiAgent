"""검색 품질 평가 (M1-06 / 4.2).

지표 (문서 단위 — 같은 문서의 여러 청크는 첫 등장 순위로 합친다)
- Recall@k : 정답 문서 중 상위 k 안에 들어온 비율          → "놓치지 않았나"
- MRR      : 첫 정답의 순위 역수 평균 (1위=1, 2위=0.5 …)   → "맨 위에 올렸나"
- nDCG@k   : 순위가 낮을수록 할인한 이득의 정규화 (이진 관련도) → "순서 전체가 좋은가"
"""

import json
import math
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from aiops.rag.models import ScoredChunk

RetrieveFn = Callable[[str, int], Awaitable[list[ScoredChunk]]]


class EvalQuery(BaseModel):
    query: str
    relevant: list[str]
    kind: str = "lexical"  # lexical: 문서 용어 그대로 / paraphrase: 표현을 바꾼 질의


class QueryResult(BaseModel):
    query: str
    kind: str = "lexical"
    ranked_docs: list[str]
    recall: float
    rr: float
    ndcg: float


class EvalReport(BaseModel):
    name: str
    k: int
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    queries: list[QueryResult]

    def misses(self) -> list[QueryResult]:
        return [q for q in self.queries if q.recall < 1.0]

    def by_kind(self) -> dict[str, float]:
        """질의 유형별 Recall@k — 어휘 일치형과 의역형에서 방식별 강약을 드러낸다."""
        kinds: dict[str, list[float]] = {}
        for q in self.queries:
            kinds.setdefault(q.kind, []).append(q.recall)
        return {k: round(sum(v) / len(v), 3) for k, v in sorted(kinds.items())}


def load_queries(path: Path) -> list[EvalQuery]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [EvalQuery.model_validate(json.loads(line)) for line in lines if line.strip()]


def dedupe_docs(hits: list[ScoredChunk]) -> list[str]:
    return list(dict.fromkeys(h.chunk.doc_id for h in hits))


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    return next((1 / i for i, d in enumerate(ranked, 1) if d in relevant), 0.0)


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1 / math.log2(i + 1) for i, d in enumerate(ranked[:k], 1) if d in relevant)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


async def evaluate(
    name: str, retrieve: RetrieveFn, queries: list[EvalQuery], k: int = 5
) -> EvalReport:
    results = []
    for q in queries:
        ranked = dedupe_docs(await retrieve(q.query, k * 4))  # 청크 중복 제거 후 문서 k 개 확보
        rel = set(q.relevant)
        results.append(
            QueryResult(
                query=q.query,
                kind=q.kind,
                ranked_docs=ranked[:k],
                recall=recall_at_k(ranked, rel, k),
                rr=reciprocal_rank(ranked, rel),
                ndcg=ndcg_at_k(ranked, rel, k),
            )
        )
    n = len(results) or 1
    return EvalReport(
        name=name,
        k=k,
        recall_at_k=round(sum(r.recall for r in results) / n, 4),
        mrr=round(sum(r.rr for r in results) / n, 4),
        ndcg_at_k=round(sum(r.ndcg for r in results) / n, 4),
        queries=results,
    )
