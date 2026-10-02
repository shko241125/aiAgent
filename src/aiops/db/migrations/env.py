"""Alembic 실행 환경 (M4-02).

- 앱 기동(aiops.db.migrate.upgrade): 이미 열린 연결을 config.attributes["connection"] 으로 받는다
- CLI(alembic -c alembic.ini ...): AIOPS_DATABASE_URL 로 async 엔진을 만들어 실행
"""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from aiops.core.config import Settings
from aiops.db.models import Base, create_engine

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite 는 ALTER 제약 → 테이블 재생성
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_cli() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or Settings().database_url
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=context.config.get_main_option("sqlalchemy.url") or Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif (conn := context.config.attributes.get("connection")) is not None:
    _run(conn)
else:
    asyncio.run(_run_cli())
