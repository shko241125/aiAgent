"""Okapi BM25 키워드 검색 (4.2) — 외부 의존성 없는 인메모리 구현.

score(q, d) = Σ IDF(t) * tf(t,d)*(k1+1) / (tf(t,d) + k1*(1 - b + b*|d|/avgdl))
에러 코드, 호스트명, 설정 키처럼 '정확히 일치해야 하는' 운영 용어에 강하다 (벡터 검색의 약점 보완).
TODO(4.2): 대용량 시 OpenSearch/Elasticsearch BM25 로 대체 (동일 인터페이스 유지).
"""

import math
from collections import Counter

from aiops.rag.models import Chunk, ScoredChunk
from aiops.rag.tokenizer import tokenize


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._chunks: list[Chunk] = []
        self._tfs: list[Counter[str]] = []
        self._lens: list[int] = []
        self._df: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._chunks)

    def add(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            tokens = tokenize(c.text)
            tf = Counter(tokens)
            self._chunks.append(c)
            self._tfs.append(tf)
            self._lens.append(len(tokens))
            self._df.update(tf.keys())

    def _idf(self, term: str) -> float:
        n, df = len(self._chunks), self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 10) -> list[ScoredChunk]:
        if not self._chunks:
            return []
        q_terms = tokenize(query)
        avgdl = sum(self._lens) / len(self._lens)
        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(self._tfs):
            s = 0.0
            for t in q_terms:
                f = tf.get(t, 0)
                if f:
                    norm = self.k1 * (1 - self.b + self.b * self._lens[i] / avgdl)
                    s += self._idf(t) * f * (self.k1 + 1) / (f + norm)
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [
            ScoredChunk(chunk=self._chunks[i], score=s, sources={"bm25": s}) for s, i in scored[:k]
        ]
