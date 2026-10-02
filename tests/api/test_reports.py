"""M4-07 운영 보고서 — 코드 집계, LLM 서술 검증, 주간 스케줄(가짜 시계)·중복 발송 없음."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from tests.api.test_incident_v2 import ALERT, SCENARIOS, scripted_llm

from aiops.core.scheduler import Scheduler, next_weekly
from aiops.llm.base import LLMResponse
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.main import build_scheduler, create_app
from aiops.services.reports import unverified_numbers, weekly_period


def test_next_weekly_respects_timezone():
    now = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)  # 금 09:00 KST
    nxt = next_weekly(now, 0, 9, 0, "Asia/Seoul")  # 다음 월 09:00 KST = 월 00:00 UTC
    assert nxt == datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    start, end, rid = weekly_period(now, "Asia/Seoul")
    assert rid == "weekly-2026-W39" and end - start == timedelta(days=7)
    assert start == datetime(2026, 9, 20, 15, 0, tzinfo=UTC)  # 9/21(월) 00:00 KST


def test_unverified_numbers_flags_invented_figures():
    facts = {"incidents": {"total": 4, "rate": 0.25}, "mttr": {"mean": 12.3}}
    assert unverified_numbers("인시던트 4건, MTTR 12.3분, 25% 자동", facts) == []
    assert unverified_numbers("인시던트 7건, 99.9% 가용성", facts) == ["7", "99.9"]


def test_report_from_real_flow_scheduler_and_single_delivery(settings):
    s = settings.model_copy(
        update={
            "remediation_verify_interval_s": 0,
            "remediation_cooldown_s": 0,
            "approval_sweep_interval_s": 0,
        }
    )
    llm = scripted_llm()
    with TestClient(create_app(s, llm=llm)) as c:
        p = c.app.state.platform
        p.source.apply_scenario(SCENARIOS["deploy-pool"].model_copy(deep=True))
        body = c.post("/api/v1/orchestrations/incident-response", json=ALERT).json()
        c.post(f"/api/v1/approvals/{body['approval']['id']}/approve", json={"actor": "kim"})

        # 서술 LLM: 사실표에 없는 숫자(42)를 지어낸다 → 경고로 드러나야 한다
        llm.push(LLMResponse(content="### 요약\n- 인시던트 1건, 42% 개선"))
        r = c.post("/api/v1/reports/generate", json={"days": 1}).json()
        f = r["facts"]
        assert f["incidents"]["total"] == 1 and f["mttr_min"]["auto_resolved"] == 1
        assert f["remediation"] == {
            "actions": 1,
            "succeeded": 1,
            "recovered": 1,
            "not_recovered": 0,
            "reverted": 0,
        }
        assert f["approvals"]["by_status"] == {"approved": 1}
        assert "| 인시던트 | 1 |" in r["markdown"] and "42" in r["markdown"]
        assert "확인되지 않은 수치: 42" in r["markdown"]
        svg = c.get(f"/api/v1/reports/{r['id']}/chart.svg")
        assert svg.headers["content-type"].startswith("image/svg+xml") and "<rect" in svg.text
        again = c.post("/api/v1/reports/generate", json={"days": 1, "end": r["period_end"]}).json()
        assert again["id"] == r["id"] and again["created_at"] == r["created_at"]  # 멱등

        # 주간 스케줄: 가짜 시계로 '월요일 09:00 KST' 를 지나가게 한다
        clock = [datetime(2026, 10, 2, 0, 0, tzinfo=UTC)]
        sch = Scheduler(clock=lambda: clock[0])
        sch.weekly("weekly_report", p.reports.run_weekly, weekday=0, hour=9, tz="Asia/Seoul")
        assert c.portal.call(sch.tick) == []  # 아직 아님
        clock[0] = datetime(2026, 10, 5, 0, 1, tzinfo=UTC)  # 월 09:01 KST
        sent_before = len(p.notifier.sent)
        assert c.portal.call(sch.tick) == ["weekly_report"]
        weekly = c.get("/api/v1/reports/weekly-2026-W40").json()
        assert weekly["delivered_at"] is not None
        assert len(p.notifier.sent) == sent_before + 1
        # 재시작 가정: 같은 주를 다시 돌려도(catch-up) 새로 만들거나 다시 보내지 않는다
        sch2 = Scheduler(clock=lambda: clock[0])
        sch2.weekly(
            "weekly_report",
            p.reports.run_weekly,
            weekday=0,
            hour=9,
            tz="Asia/Seoul",
            catch_up=True,
        )
        assert c.portal.call(sch2.tick) == ["weekly_report"]
        assert len(p.notifier.sent) == sent_before + 1
        ids = [x["id"] for x in c.get("/api/v1/reports").json()]
        assert ids.count("weekly-2026-W40") == 1


class Broken(FakeLLMProvider):
    async def chat(self, messages, **kw):
        raise RuntimeError("LLM down")


def test_template_narrative_when_llm_fails(settings):
    with TestClient(create_app(settings, llm=Broken())) as c:
        r = c.post("/api/v1/reports/generate", json={"days": 7}).json()
        assert "### 요약" in r["markdown"] and "인시던트 0건" in r["markdown"]
        assert "확인되지 않은" not in r["markdown"]


def test_build_scheduler_registers_jobs(settings):
    s = settings.model_copy(update={"report_weekly": True, "prediction_scan_interval_s": 300})
    with TestClient(create_app(s)) as c:
        sch = build_scheduler(c.app.state.platform)
        assert set(sch.jobs) == {"approval_sweep", "prediction_scan", "weekly_report"}
