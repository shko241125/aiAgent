"""스키마 관리 (M4-02 / 4.4) — create_all(개발·테스트) 또는 Alembic 마이그레이션(운영).

create_all 은 '없는 테이블 생성' 만 한다: 컬럼 추가·타입 변경은 반영되지 않아 운영 DB 와 모델이
조용히 어긋난다. 운영은 마이그레이션 이력(alembic_version)으로 스키마 버전을 관리한다.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from aiops.db.models import Base

MIGRATIONS = Path(__file__).parent / "migrations"


class SchemaDrift(RuntimeError):
    pass


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return cfg


def diff(connection: Connection) -> list:
    """현재 DB 스키마와 모델 메타데이터의 차이 (빈 리스트 = 일치)."""
    return compare_metadata(
        MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
    )


def _upgrade(connection: Connection, revision: str) -> None:
    cfg = alembic_config()
    cfg.attributes["connection"] = connection
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" not in tables and tables & set(Base.metadata.tables):
        # create_all 로 만든 기존 DB: 모델과 정확히 같을 때만 '현재 버전' 으로 표시하고 넘어간다
        if d := diff(connection):
            raise SchemaDrift(f"마이그레이션 이력 없는 DB 가 모델과 다릅니다: {d[:5]}")
        command.stamp(cfg, "head")
        return
    command.upgrade(cfg, revision)


async def upgrade(engine: AsyncEngine, revision: str = "head") -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade, revision)


async def init_schema(engine: AsyncEngine, mode: str) -> None:
    if mode == "migrate":
        await upgrade(engine)
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
