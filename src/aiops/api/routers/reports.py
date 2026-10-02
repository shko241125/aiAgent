"""운영 보고서 API (M4-07 / 2.5)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from aiops.api.auth import Role, requires
from aiops.api.deps import PlatformDep
from aiops.domain.models import utcnow
from aiops.services.reports import Report

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class GenerateRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    end: datetime | None = None  # 기본: 지금
    deliver: bool = False


@router.get("", response_model=list[Report])
async def list_reports(p: PlatformDep, limit: int = 20) -> list[Report]:
    return await p.reports.recent(limit)


@router.get("/{report_id}", response_model=Report)
async def get_report(report_id: str, p: PlatformDep) -> Report:
    if (r := await p.reports.get(report_id)) is None:
        raise HTTPException(404, "report not found")
    return r


@router.get("/{report_id}/chart.svg")
async def chart(report_id: str, p: PlatformDep) -> Response:
    if (svg := await p.reports.chart(report_id)) is None:
        raise HTTPException(404, "report not found")
    return Response(svg, media_type="image/svg+xml")


@router.post("/generate", response_model=Report)
async def generate(req: GenerateRequest, p: PlatformDep) -> Report:
    """임의 기간 보고서. 같은 기간(분 단위)이면 같은 id → 다시 만들지 않는다."""
    end = req.end or utcnow()
    if end.second or end.microsecond:  # 분 단위로 '올림' — 내리면 방금 생긴 인시던트가 빠진다
        end = end.replace(second=0, microsecond=0) + timedelta(minutes=1)
    start = end - timedelta(days=req.days)
    rid = f"adhoc-{start:%Y%m%d%H%M}-{end:%Y%m%d%H%M}"
    report = await p.reports.generate(rid, "adhoc", start, end)
    if req.deliver:
        await p.reports.deliver(report)
    return report


@router.post("/weekly/run", response_model=Report)
@requires(Role.ADMIN)
async def run_weekly(p: PlatformDep) -> Report:
    """직전 완결 주 보고서 생성·발송 (이미 했으면 그대로 반환, 재발송 없음)."""
    return await p.reports.run_weekly()
