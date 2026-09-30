"""칸반 ↔ 에이전트/오케스트레이터 통합."""

import pytest

from aiops.agents.base import AgentTask, LLMAgent
from aiops.agents.context import AgentContext
from aiops.agents.orchestration.orchestrator import Orchestrator
from aiops.agents.registry import AgentRegistry
from aiops.agents.tools.base import ToolRegistry, ToolRisk, tool
from aiops.kanban import Column, KanbanBoard
from aiops.kanban.stores import InMemoryBoardStore
from aiops.kanban.tools import build_kanban_tools
from aiops.llm.base import LLMResponse, ToolCall
from aiops.prompts.registry import PromptTemplate


class Worker(LLMAgent):
    name = "worker"
    description = "w"
    prompt_name = "w"
    tool_names = ["restart"]


@pytest.fixture
def setup(fake_llm, prompts):
    @tool(risk=ToolRisk.WRITE)
    def restart(service: str) -> str:
        """restart"""
        return "ok"

    tools = ToolRegistry()
    tools.register(restart, *build_kanban_tools())
    prompts.register(PromptTemplate(name="w", system="s", user="$input"))
    agents = AgentRegistry()
    agents.register(Worker(fake_llm, tools, prompts))
    board = KanbanBoard(InMemoryBoardStore(), "b")
    orch = Orchestrator(agents, fake_llm, prompts)
    return orch, board, fake_llm


async def test_briefing_injected_and_card_settled(setup):
    orch, board, llm = setup
    card = await board.create("작업 A", column=Column.READY, capabilities=["worker"])
    llm.push(LLMResponse(content='끝 {"ok": true}'))
    ctx = AgentContext(board=board)
    res = await orch.run_agent("worker", AgentTask(instruction="go", card_id=card.id), ctx)

    assert res.success
    user_msg = llm.calls[0][1].content
    assert "작업 카드 브리핑" in user_msg and card.id in user_msg
    done = await board.get(card.id)
    assert done.column == Column.DONE
    assert done.outputs["data"] == {"ok": True} and done.handoff.startswith("[worker] 완료")


async def test_pending_approval_blocks_card(setup):
    orch, board, llm = setup
    card = await board.create("재시작", column=Column.READY)
    llm.push(
        LLMResponse(tool_calls=[ToolCall(id="1", name="restart", arguments={"service": "x"})]),
        LLMResponse(content="승인 필요"),
    )
    await orch.run_agent(
        "worker", AgentTask(instruction="go", card_id=card.id), AgentContext(board=board)
    )
    blocked = await board.get(card.id)
    assert blocked.column == Column.BLOCKED and "restart" in blocked.blocked_reason


async def test_agent_uses_board_tools_with_injected_identity(setup):
    orch, board, llm = setup
    card = await board.create("작업", column=Column.READY)
    llm.push(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="board_note",
                    arguments={"card_id": card.id, "message": "근거 X", "decision": True},
                ),
                ToolCall(
                    id="2",
                    name="board_create_card",
                    arguments={"title": "후속 조사", "capabilities": ["worker"]},
                ),
            ]
        ),
        LLMResponse(content="done"),
    )
    await orch.run_agent(
        "worker", AgentTask(instruction="go", card_id=card.id), AgentContext(board=board)
    )
    c = await board.get(card.id)
    decision = [e for e in c.log if e.kind == "decision"][0]
    assert decision.actor == "worker"  # LLM 이 아니라 런타임이 주입한 신원
    children = [x for x in await board.cards() if x.parent_id == card.id]
    assert children and children[0].title == "후속 조사"


async def test_kanban_pull_loop_with_planner(setup):
    orch, board, llm = setup
    prompts = orch.prompts
    prompts.register(PromptTemplate(name="kanban_planner", system="$agents", user="$goal"))
    llm.push(
        LLMResponse(
            content='{"cards": [{"key": "a", "title": "A", "capability": "worker"},'
            '{"key": "b", "title": "B", "capability": "worker",'
            ' "depends_on": ["a"]}]}'
        ),
        LLMResponse(content="A 완료"),
        LLMResponse(content="B 완료"),
    )
    out = await orch.run_kanban(AgentContext(board=board), goal="목표")
    assert [r.output for r in out.results] == ["A 완료", "B 완료"]
    b = [c for c in await board.cards() if c.title == "B"][0]
    assert b.column == Column.DONE
    # B 의 프롬프트에는 선행 카드 A 의 인계 메모가 들어 있어야 한다
    assert "A 완료" in llm.calls[2][1].content


async def test_planner_without_cards_blocks_epic(setup):
    orch, board, llm = setup
    orch.prompts.register(PromptTemplate(name="kanban_planner", system="$agents", user="$goal"))
    llm.push(LLMResponse(content="계획을 세울 수 없습니다"))
    out = await orch.run_kanban(AgentContext(board=board), goal="모호한 목표")
    assert out.results == []
    [epic] = await board.cards()
    assert epic.column == Column.BLOCKED and "카드를 추출하지 못함" in epic.blocked_reason
