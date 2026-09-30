"""안정성 유틸리티 (4.6): 재시도, 타임아웃, 서킷 브레이커.

LLM/외부 API 호출은 반드시 이 계층을 거치게 해서 장애 전파를 막는다.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """지수 백오프 + 지터(jitter) 재시도."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as exc:
            if attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + random.random())
            logger.warning("retry %d/%d after %.2fs: %s", attempt, attempts, delay, exc)
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    """연속 실패가 threshold 를 넘으면 reset_after 초 동안 호출을 즉시 차단한다."""

    def __init__(self, threshold: int = 5, reset_after: float = 30.0) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.reset_after:
            return False  # half-open: 다음 호출 1회 시도 허용
        return True

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if self.is_open:
            raise CircuitOpenError("circuit is open")
        try:
            result = await fn()
        except Exception:
            self._failures += 1
            if self._failures >= self.threshold:
                self._opened_at = time.monotonic()
            raise
        self._failures = 0
        self._opened_at = None
        return result
