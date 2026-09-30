"""칸반 명시적 정책 (KB-01): 전이 규칙 · WIP 제한 · handoff 필수 · DoD · lease."""

from pydantic import BaseModel, Field

from aiops.kanban.models import Card, CardType, Column

C = Column
ALLOWED_TRANSITIONS: dict[Column, set[Column]] = {
    C.BACKLOG: {C.READY, C.BLOCKED},
    C.READY: {C.IN_PROGRESS, C.BACKLOG, C.BLOCKED},
    C.IN_PROGRESS: {C.REVIEW, C.BLOCKED, C.DONE, C.READY},
    C.REVIEW: {C.DONE, C.IN_PROGRESS, C.READY, C.BLOCKED},
    C.BLOCKED: {C.READY, C.IN_PROGRESS, C.BACKLOG},
    C.DONE: {C.READY},  # reopen
}


class PolicyViolation(ValueError):
    """정책 위반. 메시지는 에이전트가 읽고 스스로 교정할 수 있게 구체적으로 쓴다."""


class BoardPolicy(BaseModel):
    wip_limits: dict[Column, int] = Field(
        default_factory=lambda: {Column.IN_PROGRESS: 10, Column.REVIEW: 10}
    )
    per_assignee_wip: int = 2
    lease_seconds: int = 900
    handoff_required: bool = True

    def check_transition(self, card: Card, to: Column, cards: list[Card], *, handoff: str) -> None:
        if to == card.column:
            raise PolicyViolation(f"{card.id} 는 이미 {to} 입니다")
        if to not in ALLOWED_TRANSITIONS[card.column]:
            allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[card.column]))
            raise PolicyViolation(f"{card.column} → {to} 전이 불가 (허용: {allowed})")

        limit = self.wip_limits.get(to)
        if limit is not None:
            count = sum(1 for c in cards if c.column == to and c.id != card.id)
            if count >= limit:
                raise PolicyViolation(
                    f"{to} WIP 제한 초과 ({count}/{limit}) — 진행 중 작업을 먼저 끝내세요"
                )

        leaving_work = card.column == Column.IN_PROGRESS
        if self.handoff_required and leaving_work and not (handoff.strip() or card.handoff.strip()):
            raise PolicyViolation(
                "IN_PROGRESS 에서 나갈 때는 handoff 메모가 필수입니다 — "
                "다음 담당자가 이것만 읽고 이어갈 수 있게 현재 상태·결과·남은 일을 적으세요"
            )

        if to == Column.DONE:
            if not card.acceptance_done:
                todo = [a.text for a in card.acceptance if not a.done]
                raise PolicyViolation(f"완료 조건(DoD) 미충족: {todo}")
            if card.type == CardType.EPIC:
                open_children = [
                    c.id for c in cards if c.parent_id == card.id and c.column != Column.DONE
                ]
                if open_children:
                    raise PolicyViolation(f"하위 카드가 끝나지 않았습니다: {open_children}")

    def check_claim(self, card: Card, actor: str, cards: list[Card]) -> None:
        if card.column not in (Column.READY, Column.BLOCKED, Column.REVIEW):
            raise PolicyViolation(f"{card.column} 카드는 claim 할 수 없습니다 (READY 만 가능)")
        mine = [c for c in cards if c.assignee == actor and c.column == Column.IN_PROGRESS]
        if len(mine) >= self.per_assignee_wip:
            raise PolicyViolation(
                f"{actor} 의 WIP 제한({self.per_assignee_wip}) 초과: {[c.id for c in mine]}"
            )
        by_id = {c.id: c for c in cards}
        pending = [d for d in card.depends_on if by_id.get(d) and by_id[d].column != Column.DONE]
        if pending:
            raise PolicyViolation(f"선행 카드가 끝나지 않았습니다: {pending}")
