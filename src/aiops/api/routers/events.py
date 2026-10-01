"""운영 이벤트 수집 API (M2-02) — Alertmanager·CI/CD 웹훅 수신."""

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks

from aiops.api.deps import PlatformDep
from aiops.domain.models import OpsEvent, Severity, utcnow
from aiops.integrations.events import (
    AlertmanagerPayload,
    ChangePayload,
    alertmanager_to_events,
    change_to_event,
    event_to_alert,
)
from aiops.services.incident_response import find_open_incident, respond_to_alert

router = APIRouter(prefix="/api/v1/events", tags=["events"])
SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.MAJOR: 2, Severity.CRITICAL: 3}


@router.post("/alertmanager")
async def alertmanager(
    payload: AlertmanagerPayload, p: PlatformDep, background: BackgroundTasks
) -> dict:
    """Alertmanager webhook_configs 의 url 로 지정한다. 재전송돼도 한 번만 저장된다."""
    added = await p.events.add(alertmanager_to_events(payload))
    triggered, suppressed = [], []
    s = p.settings
    if s.auto_incident_on_alert:
        for e in added:
            if e.type.endswith(".resolved"):
                continue
            if SEVERITY_RANK[e.severity] < SEVERITY_RANK[Severity(s.auto_incident_min_severity)]:
                continue
            window = timedelta(minutes=s.auto_incident_dedup_minutes)
            if await find_open_incident(p, e.service, window) or e.service in triggered:
                suppressed.append(e.id)  # 같은 서비스 인시던트가 이미 진행 중 → 폭주 억제
                continue
            background.add_task(respond_to_alert, p, event_to_alert(e))
            triggered.append(e.service)
    return {
        "received": len(payload.alerts),
        "stored": len(added),
        "incident_triggered_for": triggered,
        "suppressed": suppressed,
    }


@router.post("/changes", response_model=OpsEvent)
async def change(payload: ChangePayload, p: PlatformDep) -> OpsEvent:
    """배포·설정 변경 기록. RCA 가 '이상 직전의 변경'을 원인 후보로 쓴다."""
    event = change_to_event(payload)
    await p.events.add([event])
    return event


@router.get("", response_model=list[OpsEvent])
async def list_events(
    p: PlatformDep, service: str | None = None, minutes: int = 120
) -> list[OpsEvent]:
    end = utcnow()
    return await p.events.list_events(end - timedelta(minutes=minutes), end, service)
