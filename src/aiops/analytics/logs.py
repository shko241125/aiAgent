"""로그 템플릿 추출 — Drain (M2-04 / 3.3).

He et al., "Drain: An Online Log Parsing Approach with Fixed Depth Tree" (ICWS 2017) 의 핵심을 구현.
로그 수천 줄을 '템플릿 + 개수'로 압축한다:
    "Connection to 10.0.0.5:5432 timed out after 3000ms" × 812
    → "Connection to <*> timed out after <*>ms" (812)

탐색 구조 (고정 깊이 트리 → 비교 대상을 소수 클러스터로 제한해 온라인으로 빠르게 동작)
    root ─ 토큰 수 ─ 앞쪽 토큰(depth-2 개, 숫자 포함 토큰은 <*>) ─ 잎: 클러스터 목록
잎에서 유사도(같은 위치 토큰 일치 비율)가 가장 높은 클러스터에 합치고,
다른 토큰은 <*> 로 일반화한다.
"""

import re
from collections import Counter

from pydantic import BaseModel, Field

WILDCARD = "<*>"
# 변수 마스킹 — 트리 탐색 전에 명백한 변수를 치환해 템플릿 분열을 줄인다
# 주의: 숫자 마스크는 영숫자 토큰 내부(x509, ipv4, G1)를 건드리면 안 된다 — 장애 시그니처가 깨진다
_MASKS = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),  # 시각
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),  # UUID
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),  # IP[:port]
    re.compile(r"\b0x[0-9a-f]+\b", re.I),  # hex
    re.compile(r"(?<!\w)-?\d+(?:\.\d+)?(?!\w)"),  # 독립된 숫자만 (3000ms·x509 는 제외)
]


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def preprocess(line: str) -> list[str]:
    for pat in _MASKS:
        line = pat.sub(WILDCARD, line)
    return line.split()


class LogCluster(BaseModel):
    id: int
    template: list[str]
    size: int = 0
    examples: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.template)


class DrainParser:
    def __init__(self, depth: int = 4, sim_threshold: float = 0.5, max_children: int = 100) -> None:
        self.depth = max(depth, 3)
        self.sim_threshold = sim_threshold
        self.max_children = max_children
        self.tree: dict = {}
        self.clusters: list[LogCluster] = []

    @staticmethod
    def _similarity(template: list[str], tokens: list[str]) -> tuple[float, int]:
        same = sum(1 for t, s in zip(template, tokens, strict=True) if t == s and t != WILDCARD)
        params = sum(1 for t in template if t == WILDCARD)
        return same / len(tokens), params

    def _leaf(self, tokens: list[str]) -> list[LogCluster]:
        node = self.tree.setdefault(len(tokens), {})
        for tok in tokens[: self.depth - 2]:
            key = WILDCARD if _has_digit(tok) else tok
            if key not in node:
                if len(node) >= self.max_children:  # 자식 폭주 방지 → 와일드카드 가지로
                    key = WILDCARD
                node = node.setdefault(key, {})
            else:
                node = node[key]
        return node.setdefault("__clusters__", [])

    def add(self, line: str) -> LogCluster:
        tokens = preprocess(line)
        if not tokens:
            tokens = ["<empty>"]
        leaf = self._leaf(tokens)
        best, best_key = None, (-1.0, -1)
        for c in leaf:
            sim, params = self._similarity(c.template, tokens)
            if (sim, params) > best_key:
                best, best_key = c, (sim, params)
        if best is None or best_key[0] < self.sim_threshold:
            best = LogCluster(id=len(self.clusters), template=list(tokens))
            leaf.append(best)
            self.clusters.append(best)
        else:
            best.template = [
                t if t == s else WILDCARD for t, s in zip(best.template, tokens, strict=True)
            ]
        best.size += 1
        if len(best.examples) < 3:
            best.examples.append(line)
        return best

    def parse(self, lines: list[str]) -> list[int]:
        return [self.add(line).id for line in lines]

    def top(self, k: int = 10) -> list[LogCluster]:
        return sorted(self.clusters, key=lambda c: c.size, reverse=True)[:k]


def summarize_logs(lines: list[str], k: int = 10) -> list[str]:
    """LLM·시그니처 매칭에 넘길 압축 요약: '템플릿 (×개수)'."""
    parser = DrainParser()
    parser.parse(lines)
    return [f"{c.text} (×{c.size})" for c in parser.top(k)]


def grouping_accuracy(predicted: list[int], truth: list[str]) -> float:
    """LogPAI 의 Grouping Accuracy: 예측 클러스터의 줄 집합이 정답 그룹과 정확히 같은 줄의 비율."""
    pred_groups: dict[int, set[int]] = {}
    true_groups: dict[str, set[int]] = {}
    for i, (p, t) in enumerate(zip(predicted, truth, strict=True)):
        pred_groups.setdefault(p, set()).add(i)
        true_groups.setdefault(t, set()).add(i)
    correct = sum(1 for i, p in enumerate(predicted) if pred_groups[p] == true_groups[truth[i]])
    return correct / len(predicted) if predicted else 1.0


def template_counts(lines: list[str]) -> Counter:
    parser = DrainParser()
    ids = parser.parse(lines)
    by_id = {c.id: c.text for c in parser.clusters}
    return Counter(by_id[i] for i in ids)
