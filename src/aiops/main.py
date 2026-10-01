"""FastAPI 애플리케이션 엔트리포인트.

실행: uvicorn aiops.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from aiops import __version__
from aiops.api.middleware import RequestContextMiddleware
from aiops.api.routers import agents, analytics, boards, events, health, incidents, rag
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
        app.state.platform = await build_platform(settings, llm=llm)
        yield
        await app.state.platform.aclose()

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
        llm_api.router,
    ):
        app.include_router(r)
    return app


app = create_app()
