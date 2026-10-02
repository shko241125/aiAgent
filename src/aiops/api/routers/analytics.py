"""상황 인식 및 데이터 분석 API (3.x)."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
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
from aiops.analytics.prediction import (
    CapacityForecast,
    FailurePrediction,
    RiskForecast,
    forecast_capacity,
    forecast_threshold_breach,
    predict_failure,
)
from aiops.analytics.situation import SignalSet, SituationAssessment, assess
from aiops.api.auth import Role, requires
from aiops.api.deps import PlatformDep
from aiops.domain.models import OpsEvent
from aiops.services.prediction import ServicePrediction

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


class PredictRequest(BaseModel):
    values: list[float] = Field(min_length=10)
    threshold: float


class CapacityRequest(BaseModel):
    values: list[float] = Field(min_length=20)
    capacity: float
    horizon: int = Field(default=1440, ge=1, le=20000)
    period: int | None = None


def _predictor(p):
    if p.predictor is None:
        raise HTTPException(503, "장애 예측 모델 없음 — python scripts/eval_prediction.py --save")
    return p.predictor


@router.post("/predict", response_model=FailurePrediction)
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def predict(req: PredictRequest, p: PlatformDep) -> FailurePrediction:
    """'15분 안 장애' 확률 + 근거 + 선형 외삽 기준선 (M4-06)."""
    return predict_failure(_predictor(p).model, req.values, req.threshold)


@router.get("/predict/{service}", response_model=list[ServicePrediction])
async def predict_service(service: str, p: PlatformDep) -> list[ServicePrediction]:
    """운영 데이터 소스에서 서비스 지표를 읽어 예측 (대상 지표·임계치: AIOPS_PREDICTION_TARGETS)."""
    return await _predictor(p).predict(service)


@router.post("/predict/scan", response_model=list[OpsEvent])
async def predict_scan(p: PlatformDep) -> list[OpsEvent]:
    """전체 대상 서비스 스캔 → 임계 확률 이상이면 선제 알람 이벤트 저장 (주기 실행: M4-07)."""
    return await _predictor(p).scan(p.settings.prediction_service_list)


@router.post("/capacity", response_model=CapacityForecast)
@requires(Role.VIEWER)  # 계산만 하는 POST — 조회 권한
async def capacity(req: CapacityRequest) -> CapacityForecast:
    """추세 + 계절성 용량 예측: 평균이 아니라 피크가 한도에 닿는 시점."""
    return forecast_capacity(req.values, req.capacity, req.horizon, req.period)
