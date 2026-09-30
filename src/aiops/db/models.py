"""SQL 영속 계층 (SQLAlchemy 2.0 async). 로컬 SQLite / 운영 PostgreSQL.

TODO(4.4): Alembic 마이그레이션 도입, 이벤트/메트릭 원본은 시계열 DB(TimescaleDB 등)로 분리.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from aiops.domain.models import utcnow


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    service: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions: Mapped[list[Any]] = mapped_column(default=list)
    alert_ids: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentRunRow(Base):
    """에이전트 실행 이력 — 감사(audit)·품질 평가·비용 분석용."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    incident_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # agent | pipeline | workflow | supervisor
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class KanbanCardRow(Base):
    """칸반 카드 (PLAN-0001). 조회용 컬럼 + 전체 카드 JSON. version 으로 compare-and-swap."""

    __tablename__ = "kanban_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    board_id: Mapped[str] = mapped_column(String(64), index=True)
    column: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
