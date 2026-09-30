"""칸반 보드 API (PLAN-0001 / KB-04). 사람·외부 시스템도 에이전트와 같은 보드를 본다."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from aiops.api.deps import PlatformDep
from aiops.kanban.board import BoardMetrics
from aiops.kanban.models import Card, CardType, Column, Priority
from aiops.kanban.policy import PolicyViolation

router = APIRouter(prefix="/api/v1/boards", tags=["boards"])


class CardCreate(BaseModel):
    title: str
    description: str = ""
    type: CardType = CardType.TASK
    priority: Priority = Priority.P2
    column: Column = Column.READY
    labels: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class MoveRequest(BaseModel):
    to: Column
    actor: str = "human"
    handoff: str = ""
    reason: str = ""


class ActorRequest(BaseModel):
    actor: str = "human"
    capabilities: list[str] = Field(default_factory=list)


class NoteRequest(BaseModel):
    actor: str = "human"
    message: str


async def _guard(coro):
    try:
        return await coro
    except PolicyViolation as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{board_id}")
async def get_board(board_id: str, p: PlatformDep) -> dict[str, Any]:
    board = p.board(board_id)
    return {
        "board_id": board_id,
        "columns": await board.snapshot(),
        "metrics": await board.metrics(),
    }


@router.get("/{board_id}/metrics", response_model=BoardMetrics)
async def metrics(board_id: str, p: PlatformDep) -> BoardMetrics:
    return await p.board(board_id).metrics()


@router.post("/{board_id}/cards", response_model=Card, status_code=201)
async def create_card(board_id: str, req: CardCreate, p: PlatformDep) -> Card:
    return await _guard(p.board(board_id).create(actor="human", **req.model_dump()))


@router.get("/{board_id}/cards/{card_id}", response_model=Card)
async def get_card(board_id: str, card_id: str, p: PlatformDep) -> Card:
    return await _guard(p.board(board_id).get(card_id))


@router.get("/{board_id}/cards/{card_id}/briefing", response_class=PlainTextResponse)
async def briefing(board_id: str, card_id: str, p: PlatformDep) -> str:
    return await _guard(p.board(board_id).briefing(card_id))


@router.post("/{board_id}/cards/{card_id}/move", response_model=Card)
async def move(board_id: str, card_id: str, req: MoveRequest, p: PlatformDep) -> Card:
    """예: 승인 후 BLOCKED → READY 로 옮기면 다음 Kanban 실행에서 에이전트가 이어받는다."""
    return await _guard(
        p.board(board_id).move(card_id, req.to, req.actor, handoff=req.handoff, reason=req.reason)
    )


@router.post("/{board_id}/cards/{card_id}/notes", response_model=Card)
async def note(board_id: str, card_id: str, req: NoteRequest, p: PlatformDep) -> Card:
    return await _guard(p.board(board_id).note(card_id, req.actor, req.message))


@router.post("/{board_id}/claim-next")
async def claim_next(board_id: str, req: ActorRequest, p: PlatformDep) -> dict[str, Any]:
    card = await p.board(board_id).claim_next(req.actor, set(req.capabilities))
    return {"card": card}


@router.post("/{board_id}/reap")
async def reap(board_id: str, p: PlatformDep) -> dict[str, list[str]]:
    return {"returned_to_ready": [c.id for c in await p.board(board_id).reap_expired()]}
