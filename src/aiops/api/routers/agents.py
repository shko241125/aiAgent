"""Agent / Orchestration API (1.x, 2.x)."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aiops.agents.base import AgentResult, AgentTask
from aiops.agents.context import AgentContext
from aiops.agents.orchestration.orchestrator import OrchestrationResult
from aiops.api.auth import Principal, PrincipalDep, Role
from aiops.api.deps import PlatformDep, SessionDep
from aiops.db.repositories import AgentRunRepository
from aiops.domain.models import Alert
from aiops.services.incident_response import respond_to_alert

router = APIRouter(prefix="/api/v1", tags=["agents"])


def _check_preapproval(who: Principal, tools: list[str]) -> None:
    """도구 사전 승인(approved_tools)은 곧 조치 승인이다 → approver 역할 필요 (M4-01)."""
    if tools and not who.has(Role.APPROVER):
        raise HTTPException(403, "approved_tools 지정에는 'approver' 역할이 필요합니다")


class AgentRunRequest(BaseModel):
    instruction: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    approved_tools: list[str] = Field(default_factory=list)


class IncidentResponseRequest(BaseModel):
    alert: Alert
    with_remediation: bool = True
    approved_tools: list[str] = Field(default_factory=list)


class SupervisedRequest(BaseModel):
    goal: str
    max_rounds: int = 6


class KanbanRunRequest(BaseModel):
    board_id: str
    goal: str | None = None  # 주면 Planner 가 카드로 분해 후 실행, 없으면 남은 READY 카드만 처리
    max_rounds: int = 20
    approved_tools: list[str] = Field(default_factory=list)


@router.get("/agents")
async def list_agents(p: PlatformDep) -> list[dict[str, str]]:
    return p.agents.describe()


@router.post("/agents/{name}/run", response_model=AgentResult)
async def run_agent(
    name: str,
    req: AgentRunRequest,
    p: PlatformDep,
    session: SessionDep,
    who: PrincipalDep,
) -> AgentResult:
    _check_preapproval(who, req.approved_tools)
    if name not in p.agents:
        raise HTTPException(404, f"unknown agent: {name}")
    ctx = AgentContext(long_term=p.memory, approved_tools=set(req.approved_tools))
    result = await p.orchestrator.run_agent(
        name, AgentTask(instruction=req.instruction, inputs=req.inputs), ctx
    )
    await AgentRunRepository(session).save(
        ctx, "agent", "ok" if result.success else "failed", result.model_dump(mode="json")
    )
    return result


@router.post("/orchestrations/incident-response")
async def incident_response(
    req: IncidentResponseRequest, p: PlatformDep, who: PrincipalDep
) -> dict:
    """알람 1건에 대해 전체 인시던트 대응 워크플로우를 실행한다."""
    _check_preapproval(who, req.approved_tools)
    return await respond_to_alert(
        p,
        req.alert,
        with_remediation=req.with_remediation,
        approved_tools=set(req.approved_tools),
    )


@router.post("/orchestrations/supervised", response_model=OrchestrationResult)
async def supervised(req: SupervisedRequest, p: PlatformDep):
    ctx = AgentContext(long_term=p.memory)
    return await p.orchestrator.run_supervised(req.goal, ctx, max_rounds=req.max_rounds)


@router.post("/orchestrations/kanban", response_model=OrchestrationResult)
async def kanban(req: KanbanRunRequest, p: PlatformDep, who: PrincipalDep):
    """Pull 방식: 에이전트들이 보드의 READY 카드를 능력에 맞게 당겨가 처리한다.

    이전 실행이 중단됐어도 같은 board_id 로 다시 호출하면 남은 카드부터 이어서 처리한다.
    """
    _check_preapproval(who, req.approved_tools)
    ctx = AgentContext(
        long_term=p.memory, board=p.board(req.board_id), approved_tools=set(req.approved_tools)
    )
    return await p.orchestrator.run_kanban(ctx, goal=req.goal, max_rounds=req.max_rounds)
