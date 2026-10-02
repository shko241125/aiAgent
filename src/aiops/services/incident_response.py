"""인시던트 대응 서비스 — API 요청과 알람 웹훅(백그라운드)이 같은 경로를 쓴다 (M2-02).

요청 세션에 묶이지 않도록 DB 세션을 직접 연다 → 응답이 끝난 뒤 백그라운드에서도 안전하다.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from aiops.agents.context import AgentContext
from aiops.agents.orchestration.workflows import build_incident_response, finalize_incident_board
from aiops.db.repositories import AgentRunRepository, IncidentRepository
from aiops.domain.models import Alert, Incident, IncidentStatus, utcnow

if TYPE_CHECKING:
    from aiops.core.container import Platform

OPEN_STATUSES = {IncidentStatus.OPEN, IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATING}


async def find_open_incident(p: "Platform", service: str, within: timedelta) -> Incident | None:
    """같은 서비스의 최근 미해결 인시던트 — 알람 폭주 시 인시던트를 중복 생성하지 않기 위함."""
    async with p.sessionmaker() as session:
        recent = await IncidentRepository(session).list(limit=50)
    cutoff = utcnow() - within
    return next(
        (
            i
            for i in recent
            if i.service == service
            and i.status in OPEN_STATUSES
            and i.created_at.replace(tzinfo=cutoff.tzinfo) >= cutoff
        ),
        None,
    )


async def respond_to_alert(
    p: "Platform",
    alert: Alert,
    *,
    with_remediation: bool = True,
    approved_tools: set[str] | None = None,
) -> dict[str, Any]:
    async with p.sessionmaker() as session:
        incidents = IncidentRepository(session)
        incident = await incidents.create(
            Incident(
                title=alert.title,
                service=alert.service,
                severity=alert.severity,
                alert_ids=[alert.id],
            )
        )
        await p.incidents.record(
            incident.id,
            "alert",
            "alertmanager",
            alert.title,
            severity=str(alert.severity),
            service=alert.service,
        )
        ctx = AgentContext(
            incident_id=incident.id,
            long_term=p.memory,
            approved_tools=approved_tools or set(),
            board=p.board(incident.id),
        )
        wf, cards = await build_incident_response(
            p.orchestrator,
            ctx,
            title=f"[{alert.service}] {alert.title}",
            with_remediation=with_remediation,
        )
        result = await p.orchestrator.run_workflow(
            wf, ctx, state={"alert": alert.model_dump(mode="json")}
        )
        await finalize_incident_board(ctx, result.workflow_run, cards)

        run = result.workflow_run
        rca = ctx.blackboard.read("rca.data") or {}
        det = ctx.blackboard.read("detection.data") or {}
        await incidents.update(
            incident.id,
            summary=(ctx.blackboard.read("incident.output") or "")[:4000],
            root_cause=rca.get("root_cause"),
        )
        if det:
            await p.incidents.record(
                incident.id, "detection", "detection", det.get("summary", ""), **det
            )
        if rca:
            await p.incidents.record(
                incident.id,
                "rca",
                "rca",
                rca.get("root_cause", ""),
                confidence=rca.get("confidence"),
                service=rca.get("service"),
            )
        await p.incidents.transition(
            incident.id, IncidentStatus.INVESTIGATING, "orchestrator", f"워크플로우 {run.status}"
        )
        await AgentRunRepository(session).save(
            ctx,
            "workflow",
            run.status.value,
            {"steps": {k: v.status for k, v in run.steps.items()}},
        )
    return {
        "incident_id": incident.id,
        "board_id": incident.id,
        "cards": cards,
        "run_id": ctx.run_id,
        "workflow_status": run.status,
        "steps": {k: {"status": v.status, "error": v.error} for k, v in run.steps.items()},
        "pending_approvals": sorted({t for r in result.results for t in r.pending_approvals}),
        "report": ctx.blackboard.read("report.output"),
    }
