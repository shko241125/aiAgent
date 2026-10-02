"""조치 후 효과 검증 (M3-03 / 2.3) — '진행 중인 이상'이 사라졌는가.

M1-08 의 ongoing 판정(지속 이상이 지금까지 이어지는가)을 그대로 재사용한다.
실 환경에서는 조치 반영(롤링 재시작 등)에 수 분이 걸리므로 간격을 두고 여러 번 확인한다.
"""

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from aiops.analytics.rca import RCAAnalyzer
from aiops.integrations.base import OpsSource


class VerificationResult(BaseModel):
    recovered: bool
    attempts: int
    remaining: dict[str, list[str]]  # 서비스 → 아직 이상인 메트릭
    detail: str = ""


async def verify_recovery(
    source: OpsSource,
    services: list[str],
    *,
    attempts: int = 3,
    interval_s: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> VerificationResult:
    analyzer = RCAAnalyzer(source, max_depth=0)
    remaining: dict[str, list[str]] = {}
    for i in range(1, attempts + 1):
        remaining = {}
        for svc in dict.fromkeys(services):
            ev = (await analyzer.collect(svc))[0]
            if ev.onset_min_ago is not None:
                remaining[svc] = sorted(ev.anomalous_metrics)
        if not remaining:
            return VerificationResult(
                recovered=True,
                attempts=i,
                remaining={},
                detail=f"{i}회차 확인에서 진행 중 이상 없음",
            )
        if i < attempts:
            await sleep(interval_s)
    return VerificationResult(
        recovered=False,
        attempts=attempts,
        remaining=remaining,
        detail=f"{attempts}회 확인 후에도 이상 지속: {remaining}",
    )
