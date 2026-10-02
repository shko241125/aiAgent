"""FastAPI 애플리케이션 엔트리포인트.

실행: uvicorn aiops.main:app --reload
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aiops import __version__
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
)
from aiops.api.routers import llm as llm_api
from aiops.core.config import Settings, get_settings
from aiops.core.container import build_platform
from aiops.core.logging import setup_logging
from aiops.llm.base import LLMProvider


def create_app(settings: Settings | None = None, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, json_format=settings.env == "prod")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        platform = await build_platform(settings, llm=llm)
        app.state.platform = platform
        sweeper = None
        if settings.approval_sweep_interval_s > 0:  # 승인 에스컬레이션·만료 주기 처리 (M3-05)

            async def sweep_loop():
                while True:
                    await asyncio.sleep(settings.approval_sweep_interval_s)
                    try:
                        await platform.approvals.sweep()
                    except Exception:  # noqa: BLE001 - 스윕 실패가 서버를 죽이면 안 된다
                        logging.getLogger(__name__).exception("approval sweep failed")

            sweeper = asyncio.create_task(sweep_loop())
        yield
        if sweeper:
            sweeper.cancel()
        await platform.aclose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
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
        llm_api.router,
    ):
        app.include_router(r)
    return app


app = create_app()
