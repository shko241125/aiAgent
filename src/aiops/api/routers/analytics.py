"""상황 인식 및 데이터 분석 API (3.x)."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from aiops.analytics.anomaly.base import Anomaly, AnomalyDetector
from aiops.analytics.anomaly.ensemble import EnsembleDetector
from aiops.analytics.anomaly.statistical import EWMADetector, RobustZScoreDetector
from aiops.analytics.events import EventCluster, PatternStat, correlate, deduplicate, mine_patterns
from aiops.analytics.prediction import RiskForecast, forecast_threshold_breach
from aiops.analytics.situation import SignalSet, SituationAssessment, assess
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


class ForecastRequest(BaseModel):
    values: list[float] = Field(min_length=2)
    threshold: float
    horizon_steps: int = 60


@router.post("/anomalies", response_model=list[Anomaly])
async def detect_anomalies(req: AnomalyRequest) -> list[Anomaly]:
    return _DETECTORS[req.method]().detect(req.values)


@router.post("/situation", response_model=SituationAssessment)
async def situation(signals: SignalSet) -> SituationAssessment:
    return assess(signals)


@router.post("/events", response_model=EventAnalysisResponse)
async def analyze_events(req: EventAnalysisRequest) -> EventAnalysisResponse:
    from datetime import timedelta

    events = deduplicate(req.events)
    clusters = correlate(events, window=timedelta(minutes=req.window_minutes))
    return EventAnalysisResponse(
        deduplicated=len(req.events) - len(events),
        clusters=clusters,
        patterns=mine_patterns(clusters),
    )


@router.post("/forecast", response_model=RiskForecast)
async def forecast(req: ForecastRequest) -> RiskForecast:
    return forecast_threshold_breach(req.values, req.threshold, horizon_steps=req.horizon_steps)
