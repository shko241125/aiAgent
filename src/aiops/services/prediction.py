"""예측 기반 선제 알람 (M4-06 / 3.4).

주기적으로(스케줄러, M4-07) 서비스별 지표를 읽어 'N분 안 장애 확률' 을 계산하고, 임계 확률을
넘으면 `prediction.failure_risk` 이벤트를 남긴다. 이벤트 저장소에 쌓이므로 RCA·보고서가
"이 장애는 예측됐는가" 를 함께 볼 수 있다. 같은 서비스·지표는 dedup 창 안에 한 번만 울린다.
"""

from datetime import timedelta

from pydantic import BaseModel

from aiops.analytics.prediction import FailurePrediction, predict_failure
from aiops.analytics.situation_model import LogisticModel
from aiops.domain.models import OpsEvent, Severity, utcnow
from aiops.integrations.base import OpsSource
from aiops.integrations.events import SqlEventStore

EVENT_TYPE = "prediction.failure_risk"


class ServicePrediction(BaseModel):
    service: str
    metric: str
    prediction: FailurePrediction


class PredictiveMonitor:
    def __init__(
        self,
        source: OpsSource,
        model: LogisticModel,
        targets: dict[str, float],
        *,
        alarm_threshold: float = 0.6,
        events: SqlEventStore | None = None,
        lookback_min: int = 60,
        dedup_min: int = 30,
    ) -> None:
        self.source = source
        self.model = model
        self.targets = targets  # metric -> 장애 임계치 (예: memory_usage=90)
        self.alarm_threshold = alarm_threshold
        self.events = events
        self.lookback_min = lookback_min
        self.dedup_min = dedup_min

    async def predict(self, service: str) -> list[ServicePrediction]:
        end = utcnow()
        start = end - timedelta(minutes=self.lookback_min)
        out = []
        for metric, threshold in self.targets.items():
            series = await self.source.query_range(service, metric, start, end)
            values = [p.value for p in series.points]
            if len(values) < 10:
                continue  # 근거가 부족하면 예측하지 않는다
            out.append(
                ServicePrediction(
                    service=service,
                    metric=metric,
                    prediction=predict_failure(self.model, values, threshold),
                )
            )
        return out

    async def _recently_alarmed(self, service: str, metric: str) -> bool:
        if self.events is None:
            return False
        end = utcnow()
        recent = await self.events.list_events(
            end - timedelta(minutes=self.dedup_min), end, service
        )
        return any(e.type == EVENT_TYPE and e.attributes.get("metric") == metric for e in recent)

    async def scan(self, services: list[str]) -> list[OpsEvent]:
        emitted = []
        for service in services:
            for sp in await self.predict(service):
                p = sp.prediction
                if p.probability < self.alarm_threshold:
                    continue
                if await self._recently_alarmed(service, sp.metric):
                    continue
                eta = f", 추세상 약 {p.eta_min:.0f}분 후 도달" if p.eta_min is not None else ""
                event = OpsEvent(
                    source="aiops.prediction",
                    service=service,
                    type=EVENT_TYPE,
                    severity=Severity.WARNING,
                    message=(
                        f"{sp.metric} {p.horizon_min}분 내 장애 확률 {p.probability:.0%} "
                        f"(현재 {p.current:.1f} / 임계치 {p.threshold}{eta})"
                    ),
                    attributes={
                        "metric": sp.metric,
                        "probability": p.probability,
                        "eta_min": p.eta_min,
                        "factors": p.factors,
                    },
                )
                if self.events is not None:
                    await self.events.add([event])
                emitted.append(event)
        return emitted
