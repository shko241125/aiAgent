"""M3-04 인시던트 상태 머신 · 타임라인."""

import pytest

from aiops.db.models import create_engine, create_sessionmaker, init_db
from aiops.db.repositories import IncidentRepository
from aiops.domain.models import Incident, IncidentStatus, Severity
from aiops.services.incidents import IncidentService, InvalidTransition

S = IncidentStatus


@pytest.fixture
async def svc(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/i.db")
    await init_db(engine)
    sm = create_sessionmaker(engine)
    async with sm() as s:
        inc = await IncidentRepository(s).create(
            Incident(title="t", service="order-service", severity=Severity.MAJOR)
        )
    yield IncidentService(sm), inc.id
    await engine.dispose()


async def test_transitions_are_enforced(svc):
    service, iid = svc
    await service.transition(iid, S.INVESTIGATING, "bot")
    await service.transition(iid, S.MITIGATING, "bot")
    await service.transition(iid, S.INVESTIGATING, "bot", "조치 효과 없음")  # 되돌아가기 허용
    await service.transition(iid, S.RESOLVED, "sre")
    await service.transition(iid, S.CLOSED, "sre")
    with pytest.raises(InvalidTransition, match="종결"):
        await service.transition(iid, S.INVESTIGATING, "sre")


async def test_timeline_and_mttr(svc):
    service, iid = svc
    await service.record(iid, "alert", "alertmanager", "5xx 급증")
    await service.transition(iid, S.INVESTIGATING, "bot")
    await service.record(iid, "action", "sre", "rollback", ok=True)
    await service.transition(iid, S.RESOLVED, "sre")
    tl = await service.timeline(iid)
    assert [e.kind for e in tl] == ["alert", "status", "action", "status"]
    assert tl[1].message == "open → investigating" and tl[2].data == {"ok": True}
    assert service.mttr_minutes(tl) is not None and service.mttr_minutes(tl) >= 0
