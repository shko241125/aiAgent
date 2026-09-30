"""인용 검증 (M1-09 / 2.6) — "근거 기반 답변"을 측정 가능하게 만든다.

지원 형식: 문서 id 인용 `[runbook-disk-full]`, 번호 인용 `[1]` (검색 결과 순번 → 문서 id 로 매핑)
- invalid       : 검색 결과에 없는 문서를 인용 (환각 인용) → 가장 위험한 신호
- citation_rate : 인용이 하나 이상 붙은 문장의 비율
"""

import re

from pydantic import BaseModel

CITE_RE = re.compile(r"\[([^\[\]\s]{1,80})\]")
SENT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


class CitationReport(BaseModel):
    cited: list[str]
    valid: list[str]
    invalid: list[str]
    sentences: int
    cited_sentences: int
    citation_rate: float


def validate_citations(
    answer: str, allowed: set[str], numbered: list[str] | None = None, min_sentence_len: int = 8
) -> CitationReport:
    numbered = numbered or []

    def resolve(token: str) -> str:
        if token.isdigit() and 1 <= int(token) <= len(numbered):
            return numbered[int(token) - 1]
        return token

    cited, cited_sentences, sentences = [], 0, 0
    for sent in SENT_RE.split(answer or ""):
        sent = sent.strip()
        refs = [resolve(t) for t in CITE_RE.findall(sent)]
        body = CITE_RE.sub("", sent).strip()
        if len(body) < min_sentence_len and not refs:
            continue
        sentences += 1
        cited += refs
        if any(r in allowed for r in refs):
            cited_sentences += 1
    uniq = list(dict.fromkeys(cited))
    return CitationReport(
        cited=uniq,
        valid=[c for c in uniq if c in allowed],
        invalid=[c for c in uniq if c not in allowed],
        sentences=sentences,
        cited_sentences=cited_sentences,
        citation_rate=round(cited_sentences / sentences, 3) if sentences else 0.0,
    )
