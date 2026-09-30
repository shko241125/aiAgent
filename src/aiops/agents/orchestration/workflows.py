"""사전 정의된 Agent Workflow (1.3).

incident_response:
    detection ─▶ rca ─▶ remediation ─▶ incident ─▶ report
                  (장애가 아니라고 판단되면 rca 이후 단계는 SKIPPED)

ctx.board 가 있으면 인시던트 에픽 카드 1장 + 단계별 카드를 만들어 각 단계를 카드 단위로 추적한다.
실행이 중간에 끊겨도 보드에 남은 카드와 인계 메모로 새 에이전트가 이어받을 수 있다 (PLAN-0001).
"""

from aiops.agents.context import AgentContext
from aiops.agents.orchestration.orchestrator import Orchestrator
from aiops.kanban.models import CardType, Column
from aiops.workflow.engine import StepStatus, Workflow, WorkflowRun

INCIDENT_STEPS = [
    # step_id, agent, instruction
    ("detect", "detection", "알람을 분석해 장애 여부를 판단하라"),
    ("rca", "rca", "근본 원인을 분석하라"),
    ("remediate", "remediation", "복구 조치를 계획·실행하라"),
    ("incident", "incident", "인시던트 기록과 상황 공유 메시지를 작성하라"),
    ("report", "report", "이번 인시던트의 포스트모템 보고서를 작성하라"),
]


def _is_incident(run: WorkflowRun) -> bool:
    det = run.steps["detect"].output
    # LLM 이 명시적으로 is_incident=false 라고 판단한 경우에만 중단 (불확실하면 계속 진행)
    return not (det is not None and det.data.get("is_incident") is False)


async def build_incident_response(
    orch: Orchestrator,
    ctx: AgentContext,
    *,
    title: str = "incident response",
    with_remediation: bool = True,
) -> tuple[Workflow, dict[str, str]]:
    """워크플로우와 {step_id: card_id} 를 반환. 보드가 없으면 card 매핑은 비어 있다."""
    steps_def = [s for s in INCIDENT_STEPS if with_remediation or s[0] != "remediate"]
    chain = {sid: [steps_def[i - 1][0]] if i else [] for i, (sid, _, _) in enumerate(steps_def)}

    cards: dict[str, str] = {}
    if ctx.board is not None:
        epic = await ctx.board.create(
            title,
            actor="orchestrator",
            type=CardType.EPIC,
            column=Column.READY,
            description="알람 → 탐지 → RCA → 조치 → 인시던트 기록 → 보고서",
            labels=["incident"],
        )
        cards["epic"] = epic.id
        for sid, agent, instruction in steps_def:
            card = await ctx.board.create(
                f"[{agent}] {instruction}",
                actor="orchestrator",
                column=Column.READY,
                capabilities=[agent],
                parent_id=epic.id,
                depends_on=[cards[d] for d in chain[sid]],
            )
            cards[sid] = card.id

    steps = [
        orch.agent_step(
            sid,
            agent,
            instruction,
            ctx,
            depends_on=chain[sid],
            card_id=cards.get(sid),
            condition=_is_incident if sid == "rca" else None,
        )
        for sid, agent, instruction in steps_def
    ]
    wf = Workflow(
        name="incident_response",
        steps=steps,
        description="알람 → 탐지 → RCA → 조치 → 인시던트 기록 → 보고서",
    )
    return wf, cards


async def finalize_incident_board(
    ctx: AgentContext, run: WorkflowRun, cards: dict[str, str]
) -> None:
    """건너뛴 단계 카드를 BACKLOG 로 내리고, 하위 카드 상태에 맞춰 에픽을 정리한다."""
    board = ctx.board
    if board is None or "epic" not in cards:
        return
    for sid, rec in run.steps.items():
        if rec.status == StepStatus.SKIPPED and sid in cards:
            card = await board.get(cards[sid])
            if card.column == Column.READY:
                await board.move(
                    card.id,
                    Column.BACKLOG,
                    "orchestrator",
                    handoff="선행 단계 판단(장애 아님/실패)으로 건너뜀",
                )
    epic_id = cards["epic"]
    children = [c for c in await board.cards() if c.parent_id == epic_id]
    summary = ", ".join(f"{c.id}={c.column}" for c in children)
    await board.claim(epic_id, "orchestrator")
    blocked = [c for c in children if c.column == Column.BLOCKED]
    if blocked:
        reasons = "; ".join(f"{c.id}: {c.blocked_reason}" for c in blocked)
        await board.move(
            epic_id, Column.BLOCKED, "orchestrator", reason=reasons, handoff=f"하위: {summary}"
        )
    elif all(c.column == Column.DONE for c in children):
        await board.move(epic_id, Column.DONE, "orchestrator", handoff=f"전 단계 완료: {summary}")
    elif all(c.column in (Column.DONE, Column.BACKLOG) for c in children):
        await board.move(
            epic_id,
            Column.REVIEW,
            "orchestrator",
            handoff=f"일부 단계 건너뜀 — 사람 검토 필요: {summary}",
        )
    else:
        await board.move(
            epic_id,
            Column.READY,
            "orchestrator",
            handoff=f"미완료 카드 있음 — 이어서 진행 필요: {summary}",
        )
