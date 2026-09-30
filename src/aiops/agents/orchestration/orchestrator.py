"""Agent Orchestration (1.2 협업 구조 · 1.4 오케스트레이션).

지원하는 협업 패턴:
1) Pipeline(순차)      : detection → rca → remediation … 결과를 Blackboard 로 전달
2) Workflow(DAG)       : WorkflowEngine 위에서 병렬·조건부 분기 (workflows.py 참고)
3) Supervisor(동적)    : 감독자 LLM 이 매 라운드 다음 담당 에이전트를 선택 (계층형 협업)

에이전트끼리 직접 호출하지 않고 항상 오케스트레이터를 거친다 → 추적·권한·중단 제어를 한 곳에서.
"""

import json
import logging

from pydantic import BaseModel, Field

from aiops.agents.base import AgentResult, AgentTask, extract_json
from aiops.agents.context import AgentContext
from aiops.agents.registry import AgentRegistry
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
        ctx.log("orchestrator", "handoff", to=name, instruction=task.instruction[:200])
        try:
            return await self.agents.get(name).run(task, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent %s failed", name)
            ctx.log(name, "error", error=repr(exc))
            return AgentResult(agent=name, success=False, output=repr(exc))

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
        **kwargs,
    ) -> Step:
        async def action(run: WorkflowRun) -> AgentResult:
            res = await self.run_agent(
                agent, AgentTask(instruction=instruction, inputs=run.state), ctx
            )
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
        results = [r.output for r in run.steps.values() if isinstance(r.output, AgentResult)]
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
