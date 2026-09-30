"""에이전트 실행 컨텍스트 (1.6) — 한 번의 작업(run) 동안 모든 에이전트가 공유하는 환경."""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from aiops.agents.memory.base import Blackboard, MemoryStore
from aiops.domain.models import new_id, utcnow

if TYPE_CHECKING:
    from aiops.kanban.board import KanbanBoard


class TraceEvent(BaseModel):
    ts: str = Field(default_factory=lambda: utcnow().isoformat())
    agent: str
    kind: str  # llm_call | tool_call | tool_result | handoff | result | error
    data: dict[str, Any] = Field(default_factory=dict)


class AgentContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = Field(default_factory=lambda: new_id("run"))
    session_id: str | None = None
    incident_id: str | None = None
    blackboard: Blackboard = Field(default_factory=Blackboard)  # 실행 단위 공유 상태 (휘발)
    board: "KanbanBoard | None" = None  # 작업 단위 칸반 공유칠판 (영속) — PLAN-0001
    long_term: MemoryStore | None = None
    approved_tools: set[str] = Field(default_factory=set)  # 사람이 승인한 도구
    trace: list[TraceEvent] = Field(default_factory=list)

    def log(self, agent: str, kind: str, **data: Any) -> None:
        """관측성(observability): 모든 LLM/도구 호출을 기록해 사후 분석·평가에 활용.

        TODO(4.6): OpenTelemetry span / Langfuse 등으로 내보내기.
        """
        self.trace.append(TraceEvent(agent=agent, kind=kind, data=data))


def _rebuild() -> None:
    from aiops.kanban.board import KanbanBoard  # noqa: F401 - forward ref 해석용

    AgentContext.model_rebuild()


_rebuild()
