"""FastAPI 애플리케이션 엔트리포인트.

실행: uvicorn aiops.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from aiops import __version__
from aiops.api.auth import authorize, build_authenticator
from aiops.api.middleware import RequestContextMiddleware
from aiops.api.routers import (
    agents,
    analytics,
    approvals,
    boards,
    events,
    health,
    incidents,
    rag,
    reports,
)
from aiops.api.routers import llm as llm_api
from aiops.core.config import Settings, get_settings
from aiops.core.container import Platform, build_platform
from aiops.core.logging import setup_logging
from aiops.core.scheduler import Scheduler
from aiops.llm.base import LLMProvider


def build_scheduler(p: Platform) -> Scheduler:
    """주기 작업 (M4-07): 승인 스윕(M3-05) · 예측 스캔(M4-06) · 주간 보고서(M4-07)."""
    s, sch = p.settings, Scheduler()
    if s.approval_sweep_interval_s > 0:
        sch.every("approval_sweep", s.approval_sweep_interval_s, lambda now: p.approvals.sweep())
    if s.prediction_scan_interval_s > 0 and p.predictor is not None:
        services = s.prediction_service_list
        sch.every(
            "prediction_scan", s.prediction_scan_interval_s, lambda now: p.predictor.scan(services)
        )
    if s.report_weekly and p.reports is not None:
        sch.weekly(
            "weekly_report",
            p.reports.run_weekly,
            weekday=s.report_weekday,
            hour=s.report_hour,
            tz=s.report_timezone,
            catch_up=True,  # 꺼져 있던 동안 놓친 주 보충 — 결정적 id 라 중복 발송 없음
        )
    return sch


def create_app(settings: Settings | None = None, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, json_format=settings.env == "prod")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        platform = await build_platform(settings, llm=llm)
        app.state.platform = platform
        scheduler = build_scheduler(platform)
        app.state.scheduler = scheduler
        scheduler.start()
        yield
        await scheduler.stop()
        await platform.aclose()

    authenticator = build_authenticator(settings)  # prod 무인증 설정이면 여기서 기동 거부 (M4-01)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        dependencies=[Depends(authorize)],  # 모든 API 라우트: 인증 + 역할 검사
    )
    app.state.authenticator = authenticator
    app.add_middleware(RequestContextMiddleware)
    for r in (
        health.router,
        agents.router,
        boards.router,
        analytics.router,
        rag.router,
        incidents.router,
        events.router,
        approvals.router,
        reports.router,
        llm_api.router,
    ):
        app.include_router(r)
    return app


app = create_app()
