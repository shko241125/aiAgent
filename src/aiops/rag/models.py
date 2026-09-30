from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)  # source, title, service, doc_type ...


class Chunk(BaseModel):
    id: str
    doc_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float
    sources: dict[str, float] = Field(default_factory=dict)  # retriever별 점수/순위 (디버깅용)
