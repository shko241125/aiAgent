from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aiops.core.container import Platform


def get_platform(request: Request) -> Platform:
    return request.app.state.platform


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.platform.sessionmaker() as session:
        yield session


# FastAPI 권장 방식: Annotated 로 의존성 타입 별칭을 만들어 재사용
PlatformDep = Annotated[Platform, Depends(get_platform)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
