"""이벤트 수집 (M2-02 / 2.1) — Alertmanager·CI/CD 웹훅 → OpsEvent → DB 이벤트 저장소.

- 알람은 밀어넣기(push)로 들어온다: 폴링 지연 없이, 원천 시스템에 조회 부하 없이.
- 같은 알람이 재전송돼도 id(fingerprint+startsAt)로 한 번만 저장된다 (멱등성).
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiops.db.models import OpsEventRow
from aiops.domain.models import Alert, OpsEvent, Severity, new_id, utcnow
from aiops.integrations.base import EventSource

_SEVERITY = {
    "critical": Severity.CRITICAL,
    "major": Severity.MAJOR,
    "error": Severity.MAJOR,
    "warning": Severity.WARNING,
    "info": Severity.INFO,
}


def _parse_ts(value: str | None) -> datetime:
    if not value or value.startswith("0001-"):  # Alertmanager 는 미정 시각을 0001-01-01 로 보낸다
        return utcnow()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AlertmanagerAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None  # noqa: N815 - Alertmanager 필드명 그대로
    endsAt: str | None = None  # noqa: N815
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    """Alertmanager webhook (version 4)."""

    version: str = "4"
    status: str = "firing"
    receiver: str = ""
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)
    commonLabels: dict[str, str] = Field(default_factory=dict)  # noqa: N815


class ChangePayload(BaseModel):
    """CI/CD·설정 관리 도구가 보내는 변경 이벤트 (ArgoCD notification, Jenkins post-step 등)."""

    service: str
    type: str = "deploy"  # deploy | config | rollback | scale
    version: str | None = None
    message: str = ""
    author: str | None = None
    timestamp: datetime | None = None
    id: str | None = None


def service_of(labels: dict[str, str]) -> str:
    for key in ("service", "app", "app_kubernetes_io_name", "job", "container"):
        if labels.get(key):
            return labels[key]
    return "unknown"


def alertmanager_to_events(payload: AlertmanagerPayload) -> list[OpsEvent]:
    out = []
    for a in payload.alerts:
        labels = {**payload.commonLabels, **a.labels}
        name = labels.get("alertname", "unknown")
        resolved = a.status == "resolved"
        ts = _parse_ts(a.endsAt if resolved else a.startsAt)
        out.append(
            OpsEvent(
                id=f"am-{a.fingerprint or name}-{a.startsAt}-{a.status}",
                timestamp=ts,
                source="alertmanager",
                service=service_of(labels),
                type=f"alert.{name}" + (".resolved" if resolved else ""),
                severity=_SEVERITY.get(labels.get("severity", "").lower(), Severity.WARNING),
                message=a.annotations.get("summary") or a.annotations.get("description") or name,
                attributes={"labels": labels, "annotations": a.annotations},
            )
        )
    return out


def change_to_event(c: ChangePayload) -> OpsEvent:
    msg = c.message or f"{c.service} {c.type}" + (f" {c.version}" if c.version else "")
    return OpsEvent(
        id=c.id or new_id("chg"),
        timestamp=c.timestamp or utcnow(),
        source="cicd",
        service=c.service,
        type=c.type,
        message=msg,
        attributes={k: v for k, v in {"version": c.version, "author": c.author}.items() if v},
    )


def event_to_alert(e: OpsEvent) -> Alert:
    return Alert(
        id=e.id,
        service=e.service,
        title=e.message,
        severity=e.severity,
        labels=e.attributes.get("labels", {}),
        fired_at=e.timestamp,
    )


class SqlEventStore(EventSource):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def add(self, events: list[OpsEvent]) -> list[OpsEvent]:
        """새로 저장된 이벤트만 반환 (재전송 중복은 무시)."""
        added = []
        async with self.sessionmaker() as s:
            for e in events:
                if await s.get(OpsEventRow, e.id):
                    continue
                s.add(OpsEventRow(**e.model_dump(mode="python")))
                added.append(e)
            await s.commit()
        return added

    async def list_events(
        self, start: datetime, end: datetime, service: str | None = None
    ) -> list[OpsEvent]:
        stmt = select(OpsEventRow).where(
            OpsEventRow.timestamp >= start, OpsEventRow.timestamp <= end
        )
        if service:
            stmt = stmt.where(OpsEventRow.service == service)
        async with self.sessionmaker() as s:
            rows = (await s.scalars(stmt.order_by(OpsEventRow.timestamp))).all()
        return [
            OpsEvent.model_validate({**_row_dict(r), "timestamp": _aware(r.timestamp)})
            for r in rows
        ]


def _row_dict(r: OpsEventRow) -> dict[str, Any]:
    return {
        c: getattr(r, c)
        for c in ("id", "source", "service", "type", "severity", "message", "attributes")
    }


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)  # SQLite 는 tz 를 버린다
