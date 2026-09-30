import json

from aiops.kanban import CardType, Column, KanbanBoard
from aiops.kanban.docsync import DocSync
from aiops.kanban.stores import InMemoryBoardStore

CONFIG = {"sections": {"1": "Agent"}, "milestones": [{"id": "M1", "goal": "g", "epics": ["1.1"]}]}


async def _repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "a.py").write_text("# TODO(1.1): x\n# TODO(1.1): y\n")
    (tmp_path / "docs" / "ROADMAP.md").write_text(
        "# R\n<!-- AUTO:ROADMAP -->\nstale\n<!-- /AUTO -->\n"
        "<!-- AUTO:MILESTONES -->\n<!-- /AUTO -->\n"
        "<!-- AUTO:CARDS label=plan:x -->\n<!-- /AUTO -->\n"
    )
    board = KanbanBoard(InMemoryBoardStore(), "dev")
    await board.create(
        "Agent 설계",
        id="R-1.1",
        type=CardType.EPIC,
        column=Column.READY,
        refs=["src/a.py"],
        labels=["maturity:skeleton"],
        acceptance=["기준"],
    )
    await board.create(
        "하위 작업",
        id="M1-01",
        column=Column.READY,
        parent_id="R-1.1",
        labels=["milestone:M1", "plan:x"],
    )
    return board


async def test_sync_renders_and_check_detects_staleness(tmp_path):
    board = await _repo(tmp_path)
    sync = DocSync(tmp_path, await board.cards(), CONFIG)
    first = sync.run(write=True)
    assert not first.problems and "docs/ROADMAP.md" in first.changed
    text = (tmp_path / "docs" / "ROADMAP.md").read_text()
    assert "| 1.1 | Agent 설계 | `src/a.py` | 🧩 골격 | 0/1 | 2 |" in text
    assert "| **M1** | g | 1.1 | 0/1 | ⬜ 대기 |" in text
    assert "| M1-01 | 하위 작업 | 🟦 ready |" in text
    assert "stale" not in text

    # 카드 상태가 바뀌면 문서는 stale → check 가 감지
    await board.claim("M1-01", "dev")
    again = DocSync(tmp_path, await board.cards(), CONFIG).run(write=False)
    assert "docs/ROADMAP.md" in again.changed and "docs/BOARD.md" in again.changed


async def test_validation_catches_bad_todo_and_missing_refs(tmp_path):
    board = await _repo(tmp_path)
    (tmp_path / "src" / "b.py").write_text("# TODO(9.9): nope\n")
    await board.edit("M1-01", "dev", refs=["src/moved_away.py"])
    problems = DocSync(tmp_path, await board.cards(), CONFIG).validate()
    assert any("TODO(9.9)" in p for p in problems)
    assert any("src/moved_away.py" in p for p in problems)


def test_repo_board_config_is_valid_json():
    from pathlib import Path

    cfg = json.loads((Path(__file__).parents[3] / "tracking" / "board.json").read_text())
    assert {"sections", "milestones", "policy"} <= set(cfg)
