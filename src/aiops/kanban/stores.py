"""칸반 저장소 (KB-02). 세 구현 모두 낙관적 동시성(version 비교)을 지원한다.

- InMemoryBoardStore : 테스트
- FileBoardStore     : 저장소의 개발 이슈 추적 (tracking/cards/*.json, git 으로 버전관리)
- SqlBoardStore      : 플랫폼 런타임 (인시던트 보드)
"""

import asyncio
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiops.domain.models import utcnow
from aiops.kanban.models import Card


class ConflictError(RuntimeError):
    """다른 에이전트가 먼저 카드를 수정함 — 다시 읽고 재시도해야 한다."""


class BoardStore(ABC):
    @abstractmethod
    async def get(self, card_id: str) -> Card | None: ...

    @abstractmethod
    async def list(self, board_id: str) -> list[Card]: ...

    @abstractmethod
    async def _write(self, card: Card, expected_version: int | None) -> None:
        """expected_version=None 이면 신규 생성. 버전 불일치 시 ConflictError."""

    async def save(self, card: Card, expected_version: int | None) -> Card:
        card = card.model_copy(deep=True)
        card.version = (expected_version or 0) + 1 if expected_version is not None else 1
        card.updated_at = utcnow()
        await self._write(card, expected_version)
        return card

    async def next_id(self, board_id: str, prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:8]}"


class InMemoryBoardStore(BoardStore):
    def __init__(self) -> None:
        self._cards: dict[str, Card] = {}

    async def get(self, card_id: str) -> Card | None:
        c = self._cards.get(card_id)
        return c.model_copy(deep=True) if c else None

    async def list(self, board_id: str) -> list[Card]:
        return [c.model_copy(deep=True) for c in self._cards.values() if c.board_id == board_id]

    async def _write(self, card: Card, expected_version: int | None) -> None:
        current = self._cards.get(card.id)
        _check_version(card.id, current.version if current else None, expected_version)
        self._cards[card.id] = card

    async def next_id(self, board_id: str, prefix: str) -> str:
        return _sequential_id(prefix, self._cards.keys())


class FileBoardStore(BoardStore):
    """카드 1장 = JSON 파일 1개 → git diff/merge 친화적."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cards_dir = self.root / "cards"

    def _path(self, card_id: str) -> Path:
        return self.cards_dir / f"{card_id}.json"

    def _read_sync(self, card_id: str) -> Card | None:
        p = self._path(card_id)
        return Card.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def _list_sync(self, board_id: str) -> list[Card]:
        if not self.cards_dir.exists():
            return []
        cards = [
            Card.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(self.cards_dir.glob("*.json"))
        ]
        return [c for c in cards if c.board_id == board_id]

    def _write_sync(self, card: Card, expected_version: int | None) -> None:
        current = self._read_sync(card.id)
        _check_version(card.id, current.version if current else None, expected_version)
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(card.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        tmp = self._path(card.id).with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path(card.id))  # 원자적 교체 — 쓰다 만 파일이 남지 않게

    async def get(self, card_id: str) -> Card | None:
        return await asyncio.to_thread(self._read_sync, card_id)

    async def list(self, board_id: str) -> list[Card]:
        return await asyncio.to_thread(self._list_sync, board_id)

    async def _write(self, card: Card, expected_version: int | None) -> None:
        await asyncio.to_thread(self._write_sync, card, expected_version)

    async def next_id(self, board_id: str, prefix: str) -> str:
        existing = await asyncio.to_thread(
            lambda: (
                [p.stem for p in self.cards_dir.glob("*.json")] if self.cards_dir.exists() else []
            )
        )
        return _sequential_id(prefix, existing)


class SqlBoardStore(BoardStore):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def get(self, card_id: str) -> Card | None:
        from aiops.db.models import KanbanCardRow

        async with self.sessionmaker() as s:
            row = await s.get(KanbanCardRow, card_id)
            return Card.model_validate(row.data) if row else None

    async def list(self, board_id: str) -> list[Card]:
        from aiops.db.models import KanbanCardRow

        async with self.sessionmaker() as s:
            rows = await s.scalars(select(KanbanCardRow).where(KanbanCardRow.board_id == board_id))
            return [Card.model_validate(r.data) for r in rows]

    async def _write(self, card: Card, expected_version: int | None) -> None:
        from aiops.db.models import KanbanCardRow

        data = card.model_dump(mode="json")
        async with self.sessionmaker() as s:
            if expected_version is None:
                if await s.get(KanbanCardRow, card.id):
                    raise ConflictError(f"{card.id} already exists")
                s.add(
                    KanbanCardRow(
                        id=card.id,
                        board_id=card.board_id,
                        column=card.column.value,
                        version=card.version,
                        data=data,
                    )
                )
            else:
                # compare-and-swap: 버전이 그대로일 때만 갱신
                res = await s.execute(
                    update(KanbanCardRow)
                    .where(KanbanCardRow.id == card.id, KanbanCardRow.version == expected_version)
                    .values(column=card.column.value, version=card.version, data=data)
                )
                if res.rowcount == 0:
                    raise ConflictError(f"{card.id} was modified concurrently")
            await s.commit()


def _check_version(card_id: str, current: int | None, expected: int | None) -> None:
    if expected is None and current is not None:
        raise ConflictError(f"{card_id} already exists")
    if expected is not None and current != expected:
        raise ConflictError(f"{card_id} version mismatch (expected {expected}, got {current})")


def _sequential_id(prefix: str, existing) -> str:
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    nums = [int(m.group(1)) for e in existing if (m := pat.match(e))]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:02d}"
