"""문서 분할 (4.2).

Markdown 헤딩 경계를 우선 존중하고, 길면 문단 단위로 max_chars 이하가 되게 자른다.
헤딩 경로(breadcrumb)를 청크 앞에 붙여 청크 단독으로도 문맥이 살아있게 한다.
TODO(4.2): 토큰 기준 분할, 의미 기반(semantic) 분할, 표/코드블록 보존.
"""

import re

from aiops.rag.models import Chunk, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    path: list[str] = []
    last_end, last_title = 0, ""
    for m in _HEADING.finditer(text):
        body = text[last_end : m.start()].strip()
        if body:
            sections.append((last_title, body))
        level, title = len(m.group(1)), m.group(2).strip()
        path = path[: level - 1] + [title]
        last_title, last_end = " > ".join(path), m.end()
    tail = text[last_end:].strip()
    if tail:
        sections.append((last_title, tail))
    return sections


def chunk_document(doc: Document, max_chars: int = 800, overlap: int = 100) -> list[Chunk]:
    chunks: list[Chunk] = []
    for title, body in _split_sections(doc.text):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        buf = ""
        pieces: list[str] = []
        for p in paragraphs:
            if buf and len(buf) + len(p) + 2 > max_chars:
                pieces.append(buf)
                buf = buf[-overlap:] + "\n\n" + p if overlap else p
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf:
            pieces.append(buf)
        for piece in pieces:
            text = f"[{title}]\n{piece}" if title else piece
            chunks.append(
                Chunk(
                    id=f"{doc.id}#{len(chunks)}",
                    doc_id=doc.id,
                    text=text,
                    metadata={**doc.metadata, "section": title},
                )
            )
    return chunks
