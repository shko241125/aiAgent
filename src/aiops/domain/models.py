"""플랫폼 전반에서 공유하는 도메인 모델 (운영 데이터 · 이벤트 · 인시던트)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    MAJOR = "major"
    CRITICAL = "critical"


class MetricPoint(BaseModel):
    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    service: str
    metric: str
    labels: dict[str, str] = Field(default_factory=dict)
    points: list[MetricPoint]

    @property
    def values(self) -> list[float]:
        return [p.value for p in self.points]


class OpsEvent(BaseModel):
    """알람·로그 이벤트·배포/변경 이력 등 운영 이벤트의 공통 표현."""

    id: str = Field(default_factory=lambda: new_id("evt"))
    timestamp: datetime = Field(default_factory=utcnow)
    source: str  # prometheus, loki, k8s, cicd, itsm ...
    service: str
    type: str  # e.g. "alert.cpu_high", "deploy", "log.error_burst"
    severity: Severity = Severity.INFO
    message: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class Alert(BaseModel):
    """외부 모니터링에서 유입되는 알람 (Incident 대응 파이프라인의 입력)."""

    id: str = Field(default_factory=lambda: new_id("alert"))
    service: str
    title: str
    severity: Severity = Severity.WARNING
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    fired_at: datetime = Field(default_factory=utcnow)


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: new_id("inc"))
    title: str
    service: str
    severity: Severity
    status: IncidentStatus = IncidentStatus.OPEN
    summary: str = ""
    root_cause: str | None = None
    actions: list[str] = Field(default_factory=list)
    alert_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
