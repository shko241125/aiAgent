"""칸반 보드 서비스 (KB-01~03). 모든 변경은 정책 검사 → 로그 기록 → CAS 저장 순서로 일어난다."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from aiops.domain.models import utcnow
from aiops.kanban.briefing import render_briefing
from aiops.kanban.models import (
    AcceptanceItem,
    Card,
    CardType,
    Column,
    LogKind,
    Priority,
)
from aiops.kanban.policy import BoardPolicy, PolicyViolation
from aiops.kanban.stores import BoardStore, ConflictError

PRIORITY_RANK = {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2, Priority.P3: 3}
EDITABLE = {
    "title",
    "description",
    "priority",
    "labels",
    "refs",
    "capabilities",
    "depends_on",
    "parent_id",
    "type",
}


class BoardMetrics(BaseModel):
    """칸반 흐름 지표. lead = 생성→완료, cycle = 착수→완료 (시간)."""

    counts: dict[str, int]
    wip: int
    blocked: list[dict[str, str]]
    throughput: int
    avg_lead_time_h: float | None
    avg_cycle_time_h: float | None


class KanbanBoard:
    def __init__(
        self,
        store: BoardStore,
        board_id: str,
        *,
        policy: BoardPolicy | None = None,
        prefix: str = "T",
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.board_id = board_id
        self.policy = policy or BoardPolicy()
        self.prefix = prefix
        self.clock = clock
        self._lock = asyncio.Lock()

    # ---- 조회 ----------------------------------------------------------------
    async def cards(self) -> list[Card]:
        return await self.store.list(self.board_id)

    async def get(self, card_id: str) -> Card:
        card = await self.store.get(card_id)
        if card is None or card.board_id != self.board_id:
            raise KeyError(f"card not found: {card_id}")
        return card

    async def briefing(self, card_id: str, *, commits: list[str] | None = None) -> str:
        card = await self.get(card_id)
        by_id = {c.id: c for c in await self.cards()}
        return render_briefing(card, by_id, commits=commits, now=self.clock())

    # ---- 변경 공통 -----------------------------------------------------------
    async def _mutate(self, card_id: str, fn: Callable[[Card, list[Card]], None]) -> Card:
        """읽기 → 변경 → CAS 저장. 충돌 시 최신 상태로 재시도 (다른 프로세스와의 경합 대비)."""
        async with self._lock:
            for _ in range(5):
                card = await self.get(card_id)
                all_cards = await self.cards()
                expected = card.version
                fn(card, all_cards)
                try:
                    return await self.store.save(card, expected_version=expected)
                except ConflictError:
                    continue
            raise ConflictError(f"{card_id}: too many concurrent modifications")

    # ---- 생성·편집 -----------------------------------------------------------
    async def create(
        self,
        title: str,
        *,
        actor: str = "system",
        id: str | None = None,
        description: str = "",
        type: CardType = CardType.TASK,
        priority: Priority = Priority.P2,
        column: Column = Column.BACKLOG,
        labels: list[str] | None = None,
        refs: list[str] | None = None,
        acceptance: list[str] | None = None,
        capabilities: list[str] | None = None,
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> Card:
        if column in (Column.IN_PROGRESS, Column.DONE):
            raise PolicyViolation("카드는 BACKLOG/READY/BLOCKED 로만 생성할 수 있습니다")
        card = Card(
            id=id or await self.store.next_id(self.board_id, self.prefix),
            board_id=self.board_id,
            title=title,
            description=description,
            type=type,
            priority=priority,
            column=column,
            labels=labels or [],
            refs=refs or [],
            acceptance=[AcceptanceItem(text=a) for a in acceptance or []],
            capabilities=capabilities or [],
            parent_id=parent_id,
            depends_on=depends_on or [],
            created_at=self.clock(),
        )
        card.add_log(actor, LogKind.CREATE, f"created in {column}")
        return await self.store.save(card, expected_version=None)

    async def edit(self, card_id: str, actor: str, **fields: Any) -> Card:
        """계획 변경. 무엇이 바뀌었는지 로그에 남겨 계획 이력을 추적한다."""
        unknown = set(fields) - EDITABLE - {"acceptance"}
        if unknown:
            raise PolicyViolation(f"편집할 수 없는 필드: {sorted(unknown)}")

        def fn(card: Card, _: list[Card]) -> None:
            changed = []
            for k, v in fields.items():
                if k == "acceptance":
                    old = {a.text: a.done for a in card.acceptance}
                    v = [AcceptanceItem(text=t, done=old.get(t, False)) for t in v]
                if getattr(card, k) != v:
                    setattr(card, k, v)
                    changed.append(k)
            if changed:
                card.add_log(actor, LogKind.EDIT, f"edited: {', '.join(changed)}")

        return await self._mutate(card_id, fn)

    # ---- 흐름 ----------------------------------------------------------------
    async def claim(self, card_id: str, actor: str) -> Card:
        def fn(card: Card, all_cards: list[Card]) -> None:
            self.policy.check_claim(card, actor, all_cards)
            self.policy.check_transition(card, Column.IN_PROGRESS, all_cards, handoff="-")
            prev = card.assignee
            card.column = Column.IN_PROGRESS
            card.assignee = actor
            card.blocked_reason = None
            card.lease_expires_at = self.clock() + timedelta(seconds=self.policy.lease_seconds)
            card.started_at = card.started_at or self.clock()
            card.add_log(
                actor, LogKind.CLAIM, "claimed" + (f" (이전 담당: {prev})" if prev else "")
            )

        return await self._mutate(card_id, fn)

    async def claim_next(self, actor: str, capabilities: set[str] | None = None) -> Card | None:
        """당김(Pull): 능력이 맞고 선행 작업이 끝난 READY 카드 중 우선순위가 가장 높은 것."""
        await self.reap_expired()
        caps = (capabilities or set()) | {actor}
        cards = await self.cards()
        done = {c.id for c in cards if c.column == Column.DONE}
        candidates = sorted(
            (
                c
                for c in cards
                if c.column == Column.READY
                and c.type != CardType.EPIC
                and (not c.capabilities or caps & set(c.capabilities))
                and all(d in done for d in c.depends_on)
            ),
            key=lambda c: (PRIORITY_RANK[c.priority], c.created_at),
        )
        for c in candidates:
            try:
                return await self.claim(c.id, actor)
            except (PolicyViolation, ConflictError):
                continue
        return None

    async def move(
        self, card_id: str, to: Column, actor: str, *, handoff: str = "", reason: str = ""
    ) -> Card:
        if to == Column.IN_PROGRESS:
            return await self.claim(card_id, actor)
        if to == Column.BLOCKED and not reason.strip():
            raise PolicyViolation("BLOCKED 로 옮길 때는 reason(막힌 사유)이 필수입니다")

        def fn(card: Card, all_cards: list[Card]) -> None:
            self.policy.check_transition(card, to, all_cards, handoff=handoff)
            frm = card.column
            if handoff.strip():
                card.handoff = handoff.strip()
                card.add_log(actor, LogKind.HANDOFF, handoff.strip())
            card.column = to
            card.lease_expires_at = None
            if to == Column.BLOCKED:
                card.blocked_reason = reason.strip()
                card.add_log(actor, LogKind.BLOCK, reason.strip())
            else:
                card.blocked_reason = None
            if to in (Column.READY, Column.BACKLOG):
                card.assignee = None
            if to == Column.DONE:
                card.done_at = self.clock()
            elif frm == Column.DONE:
                card.done_at = None
            card.add_log(actor, LogKind.MOVE, f"{frm} → {to}")

        return await self._mutate(card_id, fn)

    async def release(self, card_id: str, actor: str, handoff: str) -> Card:
        return await self.move(card_id, Column.READY, actor, handoff=handoff)

    async def heartbeat(self, card_id: str, actor: str) -> Card:
        def fn(card: Card, _: list[Card]) -> None:
            if card.assignee != actor or card.column != Column.IN_PROGRESS:
                raise PolicyViolation(f"{actor} 는 {card_id} 의 담당자가 아닙니다")
            card.lease_expires_at = self.clock() + timedelta(seconds=self.policy.lease_seconds)

        return await self._mutate(card_id, fn)

    async def note(
        self, card_id: str, actor: str, message: str, kind: LogKind = LogKind.NOTE
    ) -> Card:
        def fn(card: Card, _: list[Card]) -> None:
            card.add_log(actor, kind, message)

        return await self._mutate(card_id, fn)

    async def set_output(self, card_id: str, actor: str, key: str, value: Any) -> Card:
        """카드 단위 공유칠판에 결과 기록."""

        def fn(card: Card, _: list[Card]) -> None:
            card.outputs[key] = value
            card.add_log(actor, LogKind.OUTPUT, f"outputs.{key} 기록")

        return await self._mutate(card_id, fn)

    async def check(self, card_id: str, actor: str, index: int, done: bool = True) -> Card:
        def fn(card: Card, _: list[Card]) -> None:
            if not 0 <= index < len(card.acceptance):
                raise PolicyViolation(f"DoD 항목 번호 범위 밖: {index}")
            card.acceptance[index].done = done
            mark = "✔" if done else "✘"
            card.add_log(actor, LogKind.NOTE, f"DoD {mark} {card.acceptance[index].text}")

        return await self._mutate(card_id, fn)

    async def reap_expired(self) -> list[Card]:
        """lease 만료 카드를 READY 로 반환.

        죽은(응답 없는) 에이전트의 작업을 다른 에이전트가 이어받게 한다.
        """
        now = self.clock()
        expired = [
            c
            for c in await self.cards()
            if c.column == Column.IN_PROGRESS and c.lease_expires_at and c.lease_expires_at < now
        ]
        out = []
        for c in expired:

            def fn(card: Card, _: list[Card]) -> None:
                prev = card.assignee
                card.column = Column.READY
                card.assignee = None
                card.lease_expires_at = None
                card.add_log(
                    "system",
                    LogKind.LEASE_EXPIRED,
                    f"{prev} 의 lease 만료 — READY 로 반환 (handoff/log 보존)",
                )

            out.append(await self._mutate(c.id, fn))
        return out

    # ---- 지표 ----------------------------------------------------------------
    async def metrics(self) -> BoardMetrics:
        cards = await self.cards()
        done = [c for c in cards if c.column == Column.DONE and c.done_at]

        def avg_h(pairs):
            vals = [(b - a).total_seconds() / 3600 for a, b in pairs if a and b]
            return round(sum(vals) / len(vals), 2) if vals else None

        return BoardMetrics(
            counts={col.value: sum(1 for c in cards if c.column == col) for col in Column},
            wip=sum(1 for c in cards if c.column == Column.IN_PROGRESS),
            blocked=[
                {"id": c.id, "reason": c.blocked_reason or ""}
                for c in cards
                if c.column == Column.BLOCKED
            ],
            throughput=len(done),
            avg_lead_time_h=avg_h((c.created_at, c.done_at) for c in done),
            avg_cycle_time_h=avg_h((c.started_at, c.done_at) for c in done),
        )

    async def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        cards = await self.cards()
        return {
            col.value: [
                {
                    "id": c.id,
                    "title": c.title,
                    "type": c.type,
                    "priority": c.priority,
                    "assignee": c.assignee,
                    "blocked_reason": c.blocked_reason,
                }
                for c in sorted(cards, key=lambda c: (PRIORITY_RANK[c.priority], c.id))
                if c.column == col
            ]
            for col in Column
        }
