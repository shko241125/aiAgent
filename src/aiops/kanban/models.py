"""칸반 도메인 모델 (KB-01)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aiops.domain.models import utcnow


class Column(StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"


COLUMN_ORDER = list(Column)


class CardType(StrEnum):
    EPIC = "epic"
    TASK = "task"
    BUG = "bug"
    INVESTIGATION = "investigation"


class Priority(StrEnum):
    P0 = "p0"  # 즉시
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class LogKind(StrEnum):
    CREATE = "create"
    CLAIM = "claim"
    MOVE = "move"
    NOTE = "note"
    DECISION = "decision"  # 설계·판단 근거 — 다음 담당자가 반드시 알아야 할 것
    OUTPUT = "output"
    BLOCK = "block"
    HANDOFF = "handoff"
    LEASE_EXPIRED = "lease_expired"
    EDIT = "edit"


class LogEntry(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    actor: str
    kind: LogKind
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class AcceptanceItem(BaseModel):
    text: str
    done: bool = False


class Card(BaseModel):
    id: str
    board_id: str
    title: str
    description: str = ""
    type: CardType = CardType.TASK
    priority: Priority = Priority.P2
    column: Column = Column.BACKLOG
    labels: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)  # 로드맵 ID, 파일 경로
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)

    assignee: str | None = None
    lease_expires_at: datetime | None = None
    blocked_reason: str | None = None

    handoff: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)
    log: list[LogEntry] = Field(default_factory=list)

    version: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    done_at: datetime | None = None

    def add_log(self, actor: str, kind: LogKind, message: str = "", **data: Any) -> None:
        self.log.append(LogEntry(actor=actor, kind=kind, message=message, data=data))

    @property
    def acceptance_done(self) -> bool:
        return all(a.done for a in self.acceptance)

    def label_value(self, prefix: str) -> str | None:
        """`milestone:M1` 형태의 라벨에서 값 추출."""
        for label in self.labels:
            if label.startswith(prefix + ":"):
                return label.split(":", 1)[1]
        return None
