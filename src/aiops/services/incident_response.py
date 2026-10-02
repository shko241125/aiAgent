"""인시던트 대응 서비스 v2 — 탐지 → RCA → 조치 계획 → [사람 승인] → 실행 → 효과 검증 → 기록 (M3-06).

    detect ─▶ rca ─▶ plan ─▶ approve(HITL) ─▶ execute ─┐
                 └──────────────────────────────▶ incident ─▶ report
                                       (incident 는 execute '뒤에' 실행되지만, 조치가
                                        건너뛰어져도(계획 없음·거부) 함께 건너뛰지 않는다 → after)

- 승인 대기 중에는 워크플로우가 DB 체크포인트와 함께 멈춘다. 승인/거부/만료 → resume.
- 재개 시 메모리(Blackboard)는 사라졌으므로 끝난 단계의 결과로 다시 채운다
  (PLAN-0001 '기록이 곧 기억').
- 조치는 결정적 플레이북이 계획하고, 승인 요청에는 서버측 dry-run 결과가 함께 간다.
- API 요청과 알람 웹훅(백그라운드)이 같은 경로를 쓴다. DB 세션은 직접 연다.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from aiops.agents.context import AgentContext
from aiops.agents.orchestration.workflows import finalize_incident_board
from aiops.analytics.rca import RCAAnalyzer, RCACandidate
from aiops.db.repositories import AgentRunRepository, IncidentRepository
from aiops.domain.models import Alert, Incident, IncidentStatus, utcnow
from aiops.kanban.models import CardType, Column
from aiops.remediation.actions import ActionType, RemediationAction
from aiops.remediation.playbook import plan_actions
from aiops.remediation.runner import remediate
from aiops.workflow.engine import Step, StepStatus, Workflow, WorkflowRun

if TYPE_CHECKING:
    from aiops.core.container import Platform

WORKFLOW = "incident_response_v2"
OPEN_STATUSES = {IncidentStatus.OPEN, IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATING}
AGENT_STEPS = [  # step_id, agent, 지시
    ("detect", "detection", "알람을 분석해 장애 여부를 판단하라"),
    ("rca", "rca", "근본 원인을 분석하라"),
    ("incident", "incident", "인시던트 기록과 상황 공유 메시지를 작성하라"),
    ("report", "report", "이번 인시던트의 포스트모템 보고서를 작성하라"),
]
CARD_STEPS = [  # 보드 카드: (step_id, 제목, 능력)
    ("detect", "[detection] 장애 여부 판단", "detection"),
    ("rca", "[rca] 근본 원인 분석", "rca"),
    ("approve", "[human] 조치 승인", "human"),
    ("execute", "[remediation] 조치 실행·효과 검증", "remediation"),
    ("incident", "[incident] 인시던트 기록", "incident"),
    ("report", "[report] 포스트모템 보고서", "report"),
]


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


# ---- 문맥 재구성 ----------------------------------------------------------------
def make_context(p: "Platform", run_state: dict[str, Any]) -> AgentContext:
    incident_id = run_state["incident_id"]
    ctx = AgentContext(incident_id=incident_id, long_term=p.memory, board=p.board(incident_id))
    ctx.blackboard.write("service", run_state["alert"]["service"], author="orchestrator")
    return ctx


def rehydrate(ctx: AgentContext, run: WorkflowRun) -> None:
    """재개: 끝난 에이전트 단계의 결과로 Blackboard 를 다시 채운다 (기억 = 기록)."""
    for rec in run.steps.values():
        out = rec.output
        if rec.status == StepStatus.SUCCEEDED and isinstance(out, dict) and "agent" in out:
            ctx.blackboard.write(f"{out['agent']}.output", out.get("output", ""), author="resume")
            if out.get("data"):
                ctx.blackboard.write(f"{out['agent']}.data", out["data"], author="resume")
    execute = run.steps.get("execute")
    if execute and execute.status == StepStatus.SUCCEEDED:
        ctx.blackboard.write("remediation.output", _outcome_text(execute.output), author="resume")


def _outcome_text(o: dict[str, Any]) -> str:
    notes = " / ".join(o.get("notes", []))
    return f"조치 {o['action']['type']} {o['action']['service']} → {o['status']}" + (
        f" ({notes})" if notes else ""
    )


def _is_incident(run: WorkflowRun) -> bool:
    det = run.steps["detect"].output or {}
    return (det.get("data") or {}).get("is_incident") is not False  # 불확실하면 계속


def _has_auto_action(run: WorkflowRun) -> bool:
    plan = run.output("plan") or {}
    return bool(plan.get("primary")) and not plan.get("manual")


def _approved(run: WorkflowRun) -> bool:
    return bool((run.output("approve") or {}).get("approved"))


async def _move(
    ctx: AgentContext,
    card_id: str | None,
    to: Column,
    actor: str,
    *,
    handoff: str = "",
    reason: str = "",
) -> None:
    if ctx.board is None or card_id is None:
        return
    card = await ctx.board.get(card_id)
    if card.column == to:
        return
    if to in (Column.DONE, Column.REVIEW) and card.column != Column.IN_PROGRESS:
        await ctx.board.claim(card_id, actor)
    await ctx.board.move(card_id, to, actor, handoff=handoff, reason=reason)


# ---- 워크플로우 정의 ----------------------------------------------------------------
async def build_workflow(p: "Platform", ctx: AgentContext, run_state: dict[str, Any]) -> Workflow:
    o, cards = p.orchestrator, run_state.get("cards", {})
    incident_id = run_state["incident_id"]
    service = run_state["alert"]["service"]
    s = p.settings

    async def plan(run: WorkflowRun) -> dict[str, Any]:
        det, rca = (
            ctx.blackboard.read("detection.data") or {},
            ctx.blackboard.read("rca.data") or {},
        )
        if det:
            await p.incidents.record(
                incident_id, "detection", "detection", det.get("summary", ""), **det
            )
        if rca:
            await p.incidents.record(
                incident_id,
                "rca",
                "rca",
                rca.get("root_cause", ""),
                confidence=rca.get("confidence"),
                service=rca.get("service"),
            )
        raw = ctx.blackboard.read("rca.candidates")
        cands = (
            [RCACandidate.model_validate(c) for c in raw]
            if raw
            else await RCAAnalyzer(p.source).analyze(service)
        )
        actions = plan_actions(cands, namespace=s.remediation_namespace)
        primary = actions[0] if actions else None
        out: dict[str, Any] = {
            "actions": [a.model_dump(mode="json") for a in actions],
            "primary": primary.model_dump(mode="json") if primary else None,
            "manual": bool(primary and primary.type == ActionType.MANUAL),
        }
        if primary is None:
            await p.incidents.record(incident_id, "plan", "orchestrator", "자동 조치 후보 없음")
        elif out["manual"]:
            msg = f"[{service}] 자동화 불가 — 담당자 조치 필요: {primary.describe()}"
            await p.incidents.record(incident_id, "escalation", "orchestrator", msg)
            await p.notifier.info(msg)
        else:
            dry = await p.remediation.execute(primary, dry_run=True)  # 승인 화면에 실행 가능성
            out["dry_run"] = dry.model_dump(mode="json")
            await p.incidents.transition(
                incident_id,
                IncidentStatus.MITIGATING,
                "orchestrator",
                f"조치 계획: {primary.describe()}",
            )
            await p.incidents.record(
                incident_id,
                "plan",
                "orchestrator",
                primary.describe(),
                dry_run=dry.detail,
                ok=dry.ok,
            )
        return out

    async def describe(run: WorkflowRun) -> dict[str, Any]:
        plan_out = run.output("plan")
        primary = RemediationAction.model_validate(plan_out["primary"])
        rca = ctx.blackboard.read("rca.data") or {}
        summary = f"[{service}] {primary.describe()}"
        await _move(
            ctx,
            cards.get("approve"),
            Column.BLOCKED,
            "orchestrator",
            reason=f"사람 승인 대기: {summary}",
        )
        return {
            "summary": summary,
            "actions": [plan_out["primary"]],
            "dry_run": plan_out.get("dry_run", {}).get("detail"),
            "root_cause": rca.get("root_cause"),
            "incident_id": incident_id,
        }

    async def execute(run: WorkflowRun) -> dict[str, Any]:
        decision = run.output("approve")
        await _move(
            ctx,
            cards.get("approve"),
            Column.DONE,
            decision["actor"],
            handoff=f"승인 by {decision['actor']}: {decision.get('reason', '')}",
        )
        if ctx.board and cards.get("execute"):
            await ctx.board.claim(cards["execute"], "remediation-executor")
        primary = RemediationAction.model_validate(run.output("plan")["primary"])
        outcome = await remediate(
            p.remediation,
            p.source,
            primary,
            approved_by=decision["actor"],
            verify_services=[service, primary.service],
            verify_attempts=s.remediation_verify_attempts,
            verify_interval_s=s.remediation_verify_interval_s,
        )
        if outcome.result:
            await p.incidents.record(
                incident_id,
                "action",
                decision["actor"],
                f"{primary.describe()} → {outcome.result.detail}",
                ok=outcome.result.ok,
            )
        if outcome.verification:
            await p.incidents.record(
                incident_id,
                "verification",
                "orchestrator",
                outcome.verification.detail,
                recovered=outcome.verification.recovered,
            )
        if outcome.reverted:
            await p.incidents.record(
                incident_id,
                "action",
                "orchestrator",
                f"되돌림 → {outcome.reverted.detail}",
                ok=outcome.reverted.ok,
            )
        text = _outcome_text(outcome.model_dump(mode="json"))
        ctx.blackboard.write("remediation.output", text, author="orchestrator")
        if outcome.status == "recovered":
            await p.incidents.transition(
                incident_id, IncidentStatus.RESOLVED, "orchestrator", "자동 조치 후 복구 확인"
            )
            await _move(
                ctx, cards.get("execute"), Column.DONE, "remediation-executor", handoff=text
            )
        else:
            await p.incidents.transition(
                incident_id, IncidentStatus.INVESTIGATING, "orchestrator", "조치 후 미복구 — 재조사"
            )
            await p.incidents.record(incident_id, "escalation", "orchestrator", text)
            await p.notifier.info(f"[{service}] 에스컬레이션: {text}")
            await _move(
                ctx,
                cards.get("execute"),
                Column.BLOCKED,
                "remediation-executor",
                reason=f"미복구 — 사람 조사 필요: {text}",
                handoff=text,
            )
        return outcome.model_dump(mode="json")

    steps = {
        sid: o.agent_step(sid, agent, instruction, ctx, card_id=cards.get(sid))
        for sid, agent, instruction in AGENT_STEPS
    }
    steps["rca"].depends_on, steps["rca"].condition = ["detect"], _is_incident
    steps["incident"].depends_on, steps["incident"].after = ["rca"], ["execute"]
    steps["report"].depends_on = ["incident"]
    return Workflow(
        name=WORKFLOW,
        description="탐지→RCA→계획→승인→실행·검증→기록",
        steps=[
            steps["detect"],
            steps["rca"],
            Step(id="plan", action=plan, depends_on=["rca"]),
            Step(
                id="approve",
                kind="approval",
                describe=describe,
                depends_on=["plan"],
                condition=_has_auto_action,
            ),
            Step(id="execute", action=execute, depends_on=["approve"], condition=_approved),
            steps["incident"],
            steps["report"],
        ],
    )


async def rebuild(p: "Platform", run: WorkflowRun) -> Workflow:
    """재시작 후 재개용 팩토리: 같은 단계 id·같은 카드로 정의를 다시 만들고 기억을 복원한다."""
    ctx = make_context(p, run.state)
    rehydrate(ctx, run)
    return await build_workflow(p, ctx, run.state)


async def complete(p: "Platform", run: WorkflowRun) -> None:
    """워크플로우 종료 후처리 (처음 실행이든 재개든 엔진이 호출)."""
    state, cards = run.state, run.state.get("cards", {})
    incident_id = state["incident_id"]
    ctx = make_context(p, state)
    approve = run.steps.get("approve")
    if approve and approve.status == StepStatus.SUCCEEDED and not _approved(run):
        o = approve.output
        await _move(
            ctx,
            cards.get("approve"),
            Column.DONE,
            o.get("actor", "?"),
            handoff=f"거부 by {o.get('actor')}: {o.get('reason', '')}",
        )
        inc = await p.incidents.get(incident_id)
        if inc and inc.status == IncidentStatus.MITIGATING:
            await p.incidents.transition(
                incident_id,
                IncidentStatus.INVESTIGATING,
                "orchestrator",
                "조치 거부 — 사람 판단으로 진행",
            )
    plan_rec = run.steps.get("plan")  # 조치 없는 모드에는 plan 단계가 없다
    if plan_rec is None or plan_rec.status == StepStatus.SKIPPED:  # 탐지 결과만 기록
        det = (run.output("detect") or {}).get("data") or {}
        await p.incidents.record(
            incident_id, "detection", "detection", det.get("summary", ""), **det
        )
    await finalize_incident_board(ctx, run, cards)
    rca = (run.output("rca") or {}).get("data") or {}
    fields = {"summary": ((run.output("incident") or {}).get("output") or "")[:4000]}
    if rca.get("root_cause"):
        fields["root_cause"] = rca["root_cause"]
    async with p.sessionmaker() as session:
        await IncidentRepository(session).update(incident_id, **fields)
        await AgentRunRepository(session).save(
            ctx,
            "workflow",
            run.status.value,
            {"run_id": run.id, "steps": {k: v.status for k, v in run.steps.items()}},
        )
    inc = await p.incidents.get(incident_id)
    if inc and inc.status == IncidentStatus.OPEN:
        await p.incidents.transition(
            incident_id, IncidentStatus.INVESTIGATING, "orchestrator", f"워크플로우 {run.status}"
        )


def register(p: "Platform") -> None:
    async def factory(run: WorkflowRun) -> Workflow:
        return await rebuild(p, run)

    async def on_complete(run: WorkflowRun) -> None:
        await complete(p, run)

    p.orchestrator.engine.register(WORKFLOW, factory)
    p.orchestrator.engine.on_complete[WORKFLOW] = on_complete


# ---- 진입점 -----------------------------------------------------------------------
async def respond_to_alert(
    p: "Platform",
    alert: Alert,
    *,
    with_remediation: bool = True,
    approved_tools: set[str] | None = None,
) -> dict[str, Any]:
    async with p.sessionmaker() as session:
        incident = await IncidentRepository(session).create(
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
    state: dict[str, Any] = {"incident_id": incident.id, "alert": alert.model_dump(mode="json")}
    ctx = make_context(p, state)
    ctx.approved_tools = approved_tools or set()

    cards: dict[str, str] = {}
    epic = await ctx.board.create(
        f"[{alert.service}] {alert.title}",
        actor="orchestrator",
        type=CardType.EPIC,
        column=Column.READY,
        labels=["incident"],
        description="탐지 → RCA → 계획 → 승인 → 실행·검증 → 기록 → 보고서",
    )
    cards["epic"] = epic.id
    # 카드 선행 관계 = 워크플로우의 '필수' 의존성. 순서만 따르는 관계(after)까지 넣으면
    # 조치가 건너뛰어졌을 때 끝나지 않은 카드를 기다리며 claim 이 막힌다.
    card_deps = {"rca": "detect", "incident": "rca", "report": "incident"}
    for sid, title, cap in CARD_STEPS:
        if not with_remediation and sid in ("approve", "execute"):
            continue
        dep = cards.get(card_deps.get(sid, ""))
        card = await ctx.board.create(
            title,
            actor="orchestrator",
            column=Column.READY,
            capabilities=[cap],
            parent_id=epic.id,
            depends_on=[dep] if dep else [],
        )
        cards[sid] = card.id
    state["cards"] = cards

    wf = await build_workflow(p, ctx, state)
    if not with_remediation:  # 조치 없이 분석·기록만
        wf.steps = [st for st in wf.steps if st.id not in ("plan", "approve", "execute")]
        for st in wf.steps:
            st.after = [a for a in st.after if a in {x.id for x in wf.steps}]
    run = await p.orchestrator.engine.run(wf, state=state, run_id=f"ir-{incident.id}")
    approval = (await p.approvals.get(f"apr-{run.id}-approve")) if run.waiting_steps() else None
    return {
        "incident_id": incident.id,
        "board_id": incident.id,
        "cards": cards,
        "run_id": run.id,
        "workflow_status": run.status,
        "steps": {k: {"status": v.status, "error": v.error} for k, v in run.steps.items()},
        "approval": approval.model_dump(mode="json") if approval else None,
        "pending_approvals": [approval.id] if approval else [],
        "report": (run.output("report") or {}).get("output"),
    }
