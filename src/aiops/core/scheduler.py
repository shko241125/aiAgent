"""경량 스케줄러 (M4-07) — 주기 작업(interval)과 주간 작업(weekly)을 한 루프에서.

- 시계(clock)를 주입받는다 → 테스트는 가짜 시계로 `tick()` 만 호출해 '다음 주 월요일 9시' 를 검증
- 작업 실패가 루프를 죽이지 않는다 (로그만)
- 정확히 한 번(exactly-once)은 스케줄러가 아니라 **작업이 멱등**해서 보장한다
  (주간 보고서 id = weekly-<ISO주>). 그래서 기동 시 놓친 회차를 다시 돌려도(catch-up) 안전하다.

TODO(4.5): 다중 인스턴스에서 같은 작업이 동시에 도는 것을 막는 DB 리스(lease).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiops.domain.models import utcnow

logger = logging.getLogger(__name__)
JobFn = Callable[[datetime], Awaitable[object]]


def next_weekly(now: datetime, weekday: int, hour: int, minute: int, tz: str) -> datetime:
    """now 이후 처음 오는 '요일 weekday(월=0) hour:minute (tz 현지 시각)' — UTC aware 로 반환."""
    local = now.astimezone(ZoneInfo(tz))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=(weekday - local.weekday()) % 7)
    if target <= local:
        target += timedelta(days=7)
    return target.astimezone(now.tzinfo)


@dataclass
class Job:
    name: str
    fn: JobFn
    next_run: datetime
    interval_s: float | None = None  # interval 작업
    weekly: tuple[int, int, int, str] | None = None  # (weekday, hour, minute, tz)
    runs: int = 0
    failures: int = 0
    last_error: str | None = field(default=None, repr=False)

    def advance(self, now: datetime) -> None:
        if self.interval_s is not None:
            self.next_run = now + timedelta(seconds=self.interval_s)
        else:
            self.next_run = next_weekly(now, *self.weekly)


class Scheduler:
    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self.clock = clock
        self.jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None

    def every(self, name: str, seconds: float, fn: JobFn) -> Job:
        job = Job(name, fn, self.clock() + timedelta(seconds=seconds), interval_s=seconds)
        self.jobs[name] = job
        return job

    def weekly(
        self,
        name: str,
        fn: JobFn,
        *,
        weekday: int,
        hour: int,
        minute: int = 0,
        tz: str = "UTC",
        catch_up: bool = False,
    ) -> Job:
        """catch_up=True: 기동 직후 한 번 실행 (작업이 멱등이어야 한다 — 놓친 회차 보충용)."""
        spec = (weekday, hour, minute, tz)
        now = self.clock()
        job = Job(name, fn, now if catch_up else next_weekly(now, *spec), weekly=spec)
        self.jobs[name] = job
        return job

    async def tick(self) -> list[str]:
        """기한이 된 작업을 실행하고 실행한 작업 이름을 반환."""
        now = self.clock()
        ran = []
        for job in list(self.jobs.values()):
            if now < job.next_run:
                continue
            job.advance(now)  # 실행 전에 다음 회차를 정한다 — 실패해도 폭주 재시도하지 않게
            try:
                await job.fn(now)
                job.runs += 1
            except Exception as exc:  # noqa: BLE001 - 작업 실패가 스케줄러를 죽이면 안 된다
                job.failures += 1
                job.last_error = repr(exc)
                logger.exception("scheduled job %s failed", job.name)
            ran.append(job.name)
        return ran

    def seconds_until_next(self) -> float:
        if not self.jobs:
            return 60.0
        delta = min(j.next_run for j in self.jobs.values()) - self.clock()
        return max(0.5, min(60.0, delta.total_seconds()))

    async def _loop(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.seconds_until_next())

    def start(self) -> None:
        if self.jobs and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
