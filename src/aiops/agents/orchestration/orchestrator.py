"""Agent Orchestration (1.2 협업 구조 · 1.4 오케스트레이션).

지원하는 협업 패턴:
1) Pipeline(순차)      : detection → rca → remediation … 결과를 Blackboard 로 전달
2) Workflow(DAG)       : WorkflowEngine 위에서 병렬·조건부 분기 (workflows.py 참고)
3) Supervisor(동적)    : 감독자 LLM 이 매 라운드 다음 담당 에이전트를 선택 (계층형 협업)
4) Kanban(Pull)        : 목표를 카드로 분해 → 각 에이전트가 능력에 맞는 카드를 당겨감 (PLAN-0001)

task.card_id 가 있으면 어떤 패턴이든 카드 수명주기를 따른다:
    claim → 실행(브리핑 주입) → outputs 기록 → handoff 와 함께 DONE/REVIEW/BLOCKED

에이전트끼리 직접 호출하지 않고 항상 오케스트레이터를 거친다 → 추적·권한·중단 제어를 한 곳에서.
"""

import json
import logging

from pydantic import BaseModel, Field

from aiops.agents.base import AgentResult, AgentTask, extract_json
from aiops.agents.context import AgentContext
from aiops.agents.registry import AgentRegistry
from aiops.kanban.board import KanbanBoard
from aiops.kanban.models import CardType, Column
from aiops.llm.base import ChatMessage, LLMProvider
from aiops.prompts.registry import PromptRegistry
from aiops.workflow.engine import Step, Workflow, WorkflowEngine, WorkflowRun

logger = logging.getLogger(__name__)

FINISH = "FINISH"


class OrchestrationResult(BaseModel):
    run_id: str
    results: list[AgentResult] = Field(default_factory=list)
    blackboard: dict = Field(default_factory=dict)
    workflow_run: WorkflowRun | None = None


class Orchestrator:
    def __init__(
        self,
        agents: AgentRegistry,
        llm: LLMProvider,
        prompts: PromptRegistry,
        engine: WorkflowEngine | None = None,
    ) -> None:
        self.agents = agents
        self.llm = llm
        self.prompts = prompts
        self.engine = engine or WorkflowEngine()

    async def run_agent(self, name: str, task: AgentTask, ctx: AgentContext) -> AgentResult:
        ctx.log(
            "orchestrator",
            "handoff",
            to=name,
            instruction=task.instruction[:200],
            card=task.card_id,
        )
        board = ctx.board if task.card_id else None
        if board is not None:
            card = await board.get(task.card_id)
            if not (card.column == Column.IN_PROGRESS and card.assignee == name):
                await board.claim(task.card_id, name)
        try:
            res = await self.agents.get(name).run(task, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent %s failed", name)
            ctx.log(name, "error", error=repr(exc))
            res = AgentResult(agent=name, success=False, output=repr(exc))
        if board is not None:
            await self._settle_card(board, task.card_id, name, res)
        return res

    @staticmethod
    async def _settle_card(board: KanbanBoard, card_id: str, name: str, res: AgentResult) -> None:
        """실행 결과를 카드에 반영. handoff 는 다음 담당자가 읽을 '인계 메모'다."""
        card = await board.get(card_id)
        if card.column != Column.IN_PROGRESS or card.assignee != name:
            return  # 에이전트가 도구로 이미 카드를 옮김
        await board.set_output(card_id, name, "output", res.output[:4000])
        if res.data:
            await board.set_output(card_id, name, "data", res.data)
        summary = res.output.strip().replace("\n", " ")[:500]
        if res.pending_approvals:
            tools = ", ".join(sorted(set(res.pending_approvals)))
            await board.move(
                card_id,
                Column.BLOCKED,
                name,
                reason=f"사람 승인 필요: {tools}",
                handoff=f"[{name}] 승인 대기로 중단. 승인 후 READY 로 옮기면 재개. "
                f"지금까지: {summary}",
            )
        elif not res.success:
            await board.move(
                card_id,
                Column.BLOCKED,
                name,
                reason=f"에이전트 실패: {summary[:200]}",
                handoff=f"[{name}] 실패. 원인 확인 필요: {summary}",
            )
        else:
            to = Column.DONE if card.acceptance_done else Column.REVIEW
            await board.move(card_id, to, name, handoff=f"[{name}] 완료. 요약: {summary}")

    # 1) Pipeline -------------------------------------------------------------
    async def run_sequential(
        self, names: list[str], task: AgentTask, ctx: AgentContext, stop_on_failure: bool = True
    ) -> OrchestrationResult:
        out = OrchestrationResult(run_id=ctx.run_id)
        for name in names:
            res = await self.run_agent(name, task, ctx)
            out.results.append(res)
            if stop_on_failure and not res.success:
                break
        out.blackboard = ctx.blackboard.data
        return out

    # 2) Workflow (DAG) -------------------------------------------------------
    def agent_step(
        self,
        step_id: str,
        agent: str,
        instruction: str,
        ctx: AgentContext,
        depends_on: list[str] | None = None,
        card_id: str | None = None,
        **kwargs,
    ) -> Step:
        async def action(run: WorkflowRun) -> AgentResult:
            task = AgentTask(instruction=instruction, inputs=run.state, card_id=card_id)
            res = await self.run_agent(agent, task, ctx)
            if not res.success:
                raise RuntimeError(f"agent '{agent}' failed: {res.output}")
            return res

        return Step(
            id=step_id,
            action=action,
            depends_on=depends_on or [],
            description=f"agent:{agent}",
            **kwargs,
        )

    async def run_workflow(
        self, workflow: Workflow, ctx: AgentContext, state: dict | None = None
    ) -> OrchestrationResult:
        run = await self.engine.run(workflow, state)
        results = [
            AgentResult.model_validate(r.output)
            for r in run.steps.values()
            if isinstance(r.output, dict) and "agent" in r.output
        ]
        return OrchestrationResult(
            run_id=ctx.run_id, results=results, blackboard=ctx.blackboard.data, workflow_run=run
        )

    # 3) Supervisor -----------------------------------------------------------
    async def run_supervised(
        self, goal: str, ctx: AgentContext, max_rounds: int = 6, inputs: dict | None = None
    ) -> OrchestrationResult:
        out = OrchestrationResult(run_id=ctx.run_id)
        agents_desc = "\n".join(
            f"- {a['name']}: {a['description']}" for a in self.agents.describe()
        )
        progress: list[str] = []
        for _ in range(max_rounds):
            prompt = self.prompts.render(
                "supervisor",
                agents=agents_desc,
                goal=goal,
                progress="\n".join(progress) or "(아직 없음)",
            )
            resp = await self.llm.chat(
                [ChatMessage.system(prompt.system), ChatMessage.user(prompt.user)]
            )
            decision = extract_json(resp.content)
            nxt = decision.get("next", FINISH)
            ctx.log("supervisor", "decision", decision=decision)
            if nxt == FINISH or nxt not in self.agents:
                break
            res = await self.run_agent(
                nxt,
                AgentTask(instruction=decision.get("instruction") or goal, inputs=inputs or {}),
                ctx,
            )
            out.results.append(res)
            progress.append(f"[{nxt}] success={res.success}\n{res.output[:1000]}")
        out.blackboard = json.loads(json.dumps(ctx.blackboard.data, default=str))
        return out

    # 4) Kanban (Pull) --------------------------------------------------------
    async def plan_cards(self, goal: str, ctx: AgentContext) -> list[str]:
        """Planner LLM 으로 목표를 카드로 분해해 READY 로 올린다. 생성된 카드 id 목록 반환."""
        board = _require_board(ctx)
        agents_desc = "\n".join(
            f"- {a['name']}: {a['description']}" for a in self.agents.describe()
        )
        prompt = self.prompts.render("kanban_planner", agents=agents_desc, goal=goal)
        resp = await self.llm.chat(
            [ChatMessage.system(prompt.system), ChatMessage.user(prompt.user)]
        )
        plan = extract_json(resp.content).get("cards") or []
        epic = await board.create(
            goal[:120], actor="planner", type=CardType.EPIC, column=Column.READY, description=goal
        )
        key_to_id: dict[str, str] = {}
        created = []
        for item in plan:
            card = await board.create(
                item.get("title", "untitled"),
                actor="planner",
                column=Column.READY,
                description=item.get("description", ""),
                parent_id=epic.id,
                capabilities=[item["capability"]] if item.get("capability") else [],
                depends_on=[key_to_id[k] for k in item.get("depends_on", []) if k in key_to_id],
                acceptance=item.get("acceptance", []),
            )
            key_to_id[str(item.get("key", card.id))] = card.id
            created.append(card.id)
        if not created:  # 조용히 멈추지 않는다 — 막힌 이유를 보드에 드러낸다
            await board.move(
                epic.id,
                Column.BLOCKED,
                "planner",
                reason="Planner 응답에서 카드를 추출하지 못함 — 목표를 구체화하거나 직접 추가",
            )
            return created
        await board.note(epic.id, "planner", f"{len(created)}개 카드로 분해: {created}")
        return created

    async def run_kanban(
        self,
        ctx: AgentContext,
        *,
        goal: str | None = None,
        max_rounds: int = 20,
        inputs: dict | None = None,
    ) -> OrchestrationResult:
        """모든 에이전트가 더 당겨갈 카드가 없을 때까지 Pull 루프를 돈다."""
        board = _require_board(ctx)
        if goal:
            await self.plan_cards(goal, ctx)
        out = OrchestrationResult(run_id=ctx.run_id)
        for _ in range(max_rounds):
            progressed = False
            for a in self.agents.describe():
                name = a["name"]
                card = await board.claim_next(name, {name})
                if card is None:
                    continue
                progressed = True
                task = AgentTask(
                    instruction=f"{card.title}\n{card.description}".strip(),
                    inputs=inputs or {},
                    card_id=card.id,
                )
                out.results.append(await self.run_agent(name, task, ctx))
            if not progressed:
                break
        out.blackboard = json.loads(json.dumps(ctx.blackboard.data, default=str))
        return out


def _require_board(ctx: AgentContext) -> KanbanBoard:
    if ctx.board is None:
        raise ValueError("Kanban 패턴에는 ctx.board 가 필요합니다")
    return ctx.board
