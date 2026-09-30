from datetime import timedelta

import pytest

from aiops.domain.models import utcnow
from aiops.kanban import BoardPolicy, CardType, Column, KanbanBoard, PolicyViolation
from aiops.kanban.stores import ConflictError, FileBoardStore, InMemoryBoardStore


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def board(clock):
    return KanbanBoard(
        InMemoryBoardStore(), "b1", clock=clock, policy=BoardPolicy(lease_seconds=60)
    )


async def test_claim_requires_handoff_to_leave_in_progress(board):
    c = await board.create("task", column=Column.READY)
    await board.claim(c.id, "a")
    with pytest.raises(PolicyViolation, match="handoff"):
        await board.move(c.id, Column.REVIEW, "a")
    moved = await board.move(c.id, Column.REVIEW, "a", handoff="절반 완료, 남은 일: X")
    assert moved.column == Column.REVIEW and moved.handoff.startswith("절반")


async def test_done_requires_acceptance(board):
    c = await board.create("t", column=Column.READY, acceptance=["테스트 통과"])
    await board.claim(c.id, "a")
    with pytest.raises(PolicyViolation, match="DoD"):
        await board.move(c.id, Column.DONE, "a", handoff="h")
    await board.check(c.id, "a", 0)
    assert (await board.move(c.id, Column.DONE, "a", handoff="h")).column == Column.DONE


async def test_epic_done_requires_children(board):
    epic = await board.create("e", type=CardType.EPIC, column=Column.READY)
    child = await board.create("c", column=Column.READY, parent_id=epic.id)
    await board.claim(epic.id, "pm")
    with pytest.raises(PolicyViolation, match="하위"):
        await board.move(epic.id, Column.DONE, "pm", handoff="h")
    await board.claim(child.id, "dev")
    await board.move(child.id, Column.DONE, "dev", handoff="h")
    assert (await board.move(epic.id, Column.DONE, "pm", handoff="h")).column == Column.DONE


async def test_pull_respects_priority_capability_and_dependencies(board):
    first = await board.create("first", column=Column.READY, capabilities=["rca"])
    await board.create("second", column=Column.READY, capabilities=["rca"], depends_on=[first.id])
    await board.create("other", column=Column.READY, capabilities=["report"])
    urgent = await board.create("urgent", column=Column.READY, priority="p0")

    assert (await board.claim_next("rca")).id == urgent.id  # 능력 제한 없는 p0 우선
    assert (await board.claim_next("rca")).id == first.id
    assert await board.claim_next("rca") is None  # WIP(2) 초과 + second 는 선행 미완료


async def test_blocked_requires_reason(board):
    c = await board.create("t", column=Column.READY)
    with pytest.raises(PolicyViolation, match="reason"):
        await board.move(c.id, Column.BLOCKED, "a")
    b = await board.move(c.id, Column.BLOCKED, "a", reason="API 키 없음")
    assert b.blocked_reason == "API 키 없음"


async def test_memoryless_agent_resumes_after_lease_expiry(board, clock):
    """PLAN-0001 합격 기준: A 가 사라져도 새 에이전트 B 가 카드만 보고 이어받는다."""
    c = await board.create(
        "RCA 수행",
        column=Column.READY,
        capabilities=["rca"],
        acceptance=["원인 가설 3개", "근거 로그 첨부"],
    )
    await board.claim_next("rca-worker-A", {"rca"})
    await board.note(c.id, "rca-worker-A", "가설1: DB 커넥션 풀 고갈 (로그 근거 있음)")
    await board.set_output(c.id, "rca-worker-A", "hypotheses", ["pool exhaustion"])
    await board.check(c.id, "rca-worker-A", 0)
    # A 가 죽음 — heartbeat 없음
    clock.advance(120)

    b_card = await board.claim_next("rca-worker-B", {"rca"})  # 새 인스턴스, 기억 없음
    assert b_card.id == c.id and b_card.assignee == "rca-worker-B"
    brief = await board.briefing(c.id)
    assert "lease 만료" in brief and "pool exhaustion" in brief
    assert "[x] 원인 가설 3개" in brief and "[ ] 근거 로그 첨부" in brief
    assert "rca-worker-A 의 lease 만료" in brief  # 누가 무엇을 하다 멈췄는지 드러난다
    assert "가설1: DB 커넥션 풀 고갈" in brief


async def test_briefing_includes_dependency_handoff(board):
    a = await board.create("탐지", column=Column.READY)
    b = await board.create("RCA", column=Column.READY, depends_on=[a.id])
    await board.claim(a.id, "det")
    await board.set_output(a.id, "det", "severity", "major")
    await board.move(a.id, Column.DONE, "det", handoff="order-service 지연, deploy 직후")
    brief = await board.briefing(b.id)
    assert "order-service 지연" in brief and "outputs.severity" in brief


async def test_edit_logs_plan_changes(board):
    c = await board.create("t", acceptance=["a"])
    c = await board.edit(c.id, "pm", title="t2", acceptance=["a", "b"])
    assert c.title == "t2" and [a.text for a in c.acceptance] == ["a", "b"]
    assert c.log[-1].message == "edited: title, acceptance"


async def test_metrics(board, clock):
    c = await board.create("t", column=Column.READY)
    await board.claim(c.id, "a")
    clock.advance(3600)
    await board.move(c.id, Column.DONE, "a", handoff="h")
    m = await board.metrics()
    assert m.throughput == 1 and m.avg_cycle_time_h == 1.0


async def test_file_store_roundtrip_and_conflict(tmp_path):
    store = FileBoardStore(tmp_path)
    board = KanbanBoard(store, "dev", prefix="KB")
    c = await board.create("파일 카드", column=Column.READY)
    assert c.id == "KB-01" and (tmp_path / "cards" / "KB-01.json").exists()
    assert (await board.create("두번째")).id == "KB-02"
    stale = await store.get(c.id)
    await board.note(c.id, "a", "update")
    with pytest.raises(ConflictError):
        await store.save(stale, expected_version=stale.version)
