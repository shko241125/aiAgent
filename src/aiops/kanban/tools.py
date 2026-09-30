"""에이전트용 칸반 도구 (KB-03). 호출자(actor)는 ToolRuntime 으로 주입되어 LLM 이 위조할 수 없다."""

from aiops.agents.tools.base import Tool, ToolRuntime, tool
from aiops.kanban.models import Column, LogKind
from aiops.kanban.policy import PolicyViolation

KANBAN_TOOL_NAMES = [
    "board_list",
    "board_view",
    "board_note",
    "board_set_output",
    "board_check",
    "board_move",
    "board_create_card",
]


def _board(runtime: ToolRuntime | None):
    board = getattr(runtime.ctx, "board", None) if runtime else None
    if board is None:
        raise PolicyViolation("이 실행에는 칸반 보드가 연결되어 있지 않습니다")
    return board


def build_kanban_tools() -> list[Tool]:
    @tool(tags={"kanban"})
    async def board_list(column: str | None = None, runtime: ToolRuntime = None) -> dict:
        """보드의 카드 목록을 컬럼별로 조회한다. column 을 주면 해당 컬럼만."""
        snap = await _board(runtime).snapshot()
        return {column: snap.get(column, [])} if column else snap

    @tool(tags={"kanban"})
    async def board_view(card_id: str, runtime: ToolRuntime = None) -> str:
        """카드 브리핑(목표·DoD·선행 결과·인계 메모·이력)을 읽는다."""
        return await _board(runtime).briefing(card_id)

    @tool(tags={"kanban"})
    async def board_note(
        card_id: str, message: str, decision: bool = False, runtime: ToolRuntime = None
    ) -> str:
        """카드에 작업 기록을 남긴다. 설계·판단 근거는 decision=true 로 남겨라."""
        kind = LogKind.DECISION if decision else LogKind.NOTE
        await _board(runtime).note(card_id, runtime.agent, message, kind)
        return "ok"

    @tool(tags={"kanban"})
    async def board_set_output(
        card_id: str, key: str, value: str, runtime: ToolRuntime = None
    ) -> str:
        """카드 공유칠판(outputs)에 결과물을 기록한다. 다음 카드 담당자가 읽는다."""
        await _board(runtime).set_output(card_id, runtime.agent, key, value)
        return "ok"

    @tool(tags={"kanban"})
    async def board_check(card_id: str, index: int, runtime: ToolRuntime = None) -> str:
        """카드의 완료 조건(DoD) index 번 항목을 완료로 체크한다."""
        await _board(runtime).check(card_id, runtime.agent, index)
        return "ok"

    @tool(tags={"kanban"})
    async def board_move(
        card_id: str, to: str, handoff: str = "", reason: str = "", runtime: ToolRuntime = None
    ) -> str:
        """카드를 다른 컬럼으로 옮긴다 (ready/review/blocked/done/backlog).

        IN_PROGRESS 에서 나갈 때 handoff(인계 메모) 필수, blocked 는 reason 필수.
        """
        card = await _board(runtime).move(
            card_id, Column(to), runtime.agent, handoff=handoff, reason=reason
        )
        return f"{card.id} → {card.column}"

    @tool(tags={"kanban"})
    async def board_create_card(
        title: str,
        description: str = "",
        capabilities: list[str] | None = None,
        depends_on: list[str] | None = None,
        acceptance: list[str] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """하위 작업 카드를 READY 로 생성한다 (현재 카드가 상위가 됨). 생성된 카드 id 반환."""
        card = await _board(runtime).create(
            title,
            actor=runtime.agent,
            description=description,
            column=Column.READY,
            capabilities=capabilities or [],
            depends_on=depends_on or [],
            acceptance=acceptance or [],
            parent_id=runtime.card_id,
        )
        return card.id

    return [
        board_list,
        board_view,
        board_note,
        board_set_output,
        board_check,
        board_move,
        board_create_card,
    ]
