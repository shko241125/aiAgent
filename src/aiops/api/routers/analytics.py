"""상황 인식 및 데이터 분석 API (3.x)."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector
from aiops.analytics.anomaly.ensemble import EnsembleDetector
from aiops.analytics.anomaly.statistical import EWMADetector, RobustZScoreDetector
from aiops.analytics.events import (
    CompressionReport,
    EventCluster,
    PatternStat,
    alert_compression,
    correlate,
    deduplicate,
    mine_patterns,
)
from aiops.analytics.logs import DrainParser
from aiops.analytics.prediction import RiskForecast, forecast_threshold_breach
from aiops.analytics.situation import SignalSet, SituationAssessment, assess
from aiops.api.auth import Role, requires
from aiops.domain.models import OpsEvent

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_DETECTORS: dict[str, type[AnomalyDetector]] = {
    "ensemble": EnsembleDetector,
    "robust_zscore": RobustZScoreDetector,
    "ewma": EWMADetector,
}


class AnomalyRequest(BaseModel):
    values: list[float] = Field(min_length=3)
    method: Literal["ensemble", "robust_zscore", "ewma"] = "ensemble"


class EventAnalysisRequest(BaseModel):
    events: list[OpsEvent]
    window_minutes: int = 10


class EventAnalysisResponse(BaseModel):
    deduplicated: int
    clusters: list[EventCluster]
    patterns: list[PatternStat]
    compression: CompressionReport


class LogTemplateRequest(BaseModel):
    lines: list[str] = Field(min_length=1)
    top_k: int = 20


class ForecastRequest(BaseModel):
    values: list[float] = Field(min_length=2)
    threshold: float
    horizon_steps: int = 60


@router.post("/anomalies", response_model=list[Anomaly])
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def detect_anomalies(req: AnomalyRequest) -> list[Anomaly]:
    return _DETECTORS[req.method]().detect(req.values)


@router.post("/situation", response_model=SituationAssessment)
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def situation(signals: SignalSet) -> SituationAssessment:
    return assess(signals)


@router.post("/events", response_model=EventAnalysisResponse)
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def analyze_events(req: EventAnalysisRequest) -> EventAnalysisResponse:
    window = timedelta(minutes=req.window_minutes)
    events = deduplicate([e.model_copy(deep=True) for e in req.events], window=window)
    clusters = correlate(events, window=window)
    return EventAnalysisResponse(
        deduplicated=len(req.events) - len(events),
        clusters=clusters,
        patterns=mine_patterns(clusters),
        compression=alert_compression(req.events, window=window),
    )


@router.post("/log-templates")
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def log_templates(req: LogTemplateRequest) -> list[dict]:
    """로그 줄 → Drain 템플릿 (M2-04)."""
    parser = DrainParser()
    parser.parse(req.lines)
    return [
        {"template": c.text, "count": c.size, "examples": c.examples} for c in parser.top(req.top_k)
    ]


@router.post("/forecast", response_model=RiskForecast)
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def forecast(req: ForecastRequest) -> RiskForecast:
    return forecast_threshold_breach(req.values, req.threshold, horizon_steps=req.horizon_steps)
