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


class KnowledgeDocRow(Base):
    """운영 중 축적된 지식 문서 (해결된 인시던트 포스트모템 등). 시작 시 RAG 로 재적재된다."""

    __tablename__ = "knowledge_docs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OpsEventRow(Base):
    """웹훅으로 수집한 운영 이벤트 (알람·배포·설정 변경) — RCA 의 change 신호 원천 (M2-02)."""

    __tablename__ = "ops_events"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)  # 재전송 중복 제거 키
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(32))
    service: Mapped[str] = mapped_column(String(128), index=True)
    type: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)


class WorkflowRunRow(Base):
    """워크플로우 실행 체크포인트 (M3-01) — 승인 대기·재시작 후 재개의 기반."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IncidentEventRow(Base):
    """인시던트 타임라인 (M3-04) — 탐지·RCA·승인·조치·검증·상태 변화가 시간순으로 남는다."""

    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ApprovalRow(Base):
    """사람 승인 요청 (M3-05) — 워크플로우 승인 단계 1개당 1건 (id 결정적 → 중복 요청 방지)."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    step_id: Mapped[str] = mapped_column(String(64))
    incident_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # pending|approved|rejected|expired
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
