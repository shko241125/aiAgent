"""M4-02 Alembic 마이그레이션 — 모델 ↔ 마이그레이션 드리프트를 테스트가 잡는다."""

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from aiops.db.migrate import SchemaDrift, alembic_config, diff, upgrade
from aiops.db.models import create_engine, init_db


@pytest.fixture
def url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path}/m.db"


def head() -> str:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"마이그레이션 head 가 갈라졌습니다: {heads} (alembic merge 필요)"
    return heads[0]


async def version(engine) -> str:
    async with engine.connect() as conn:
        return (await conn.execute(text("select version_num from alembic_version"))).scalar_one()


async def test_migrations_match_models(url):
    """모델을 고치고 마이그레이션을 안 만들면 여기서 실패한다 → alembic revision --autogenerate."""
    engine = create_engine(url)
    await upgrade(engine)
    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []
    assert await version(engine) == head()
    await upgrade(engine)  # 두 번 적용해도 무해 (기동할 때마다 호출된다)
    assert await version(engine) == head()
    await engine.dispose()


async def test_existing_create_all_db_is_stamped(url):
    engine = create_engine(url)
    await init_db(engine, "create_all")  # 마이그레이션 도입 전 DB
    await init_db(engine, "migrate")
    assert await version(engine) == head()
    await engine.dispose()


async def test_unversioned_db_that_differs_is_refused(url):
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("create table incidents (id varchar(32) primary key)"))
    with pytest.raises(SchemaDrift):
        await init_db(engine, "migrate")
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "alembic_version" not in tables  # 어설프게 표시하지 않는다
    await engine.dispose()


def test_schema_mode_defaults(settings):
    assert settings.schema_mode == "create_all"
    assert settings.model_copy(update={"env": "prod"}).schema_mode == "migrate"
