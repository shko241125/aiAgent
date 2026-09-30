"""개발 보드 CLI (KB-05) — 사람과 코딩 에이전트(Claude Code 등)가 같은 방법론으로 일한다.

    python -m aiops.kanban.cli brief                 # 세션 시작 브리핑 (훅이 자동 실행)
    python -m aiops.kanban.cli show M1-03            # 카드 브리핑 + 관련 커밋
    python -m aiops.kanban.cli next --cap rag        # 능력에 맞는 카드 당겨오기
    python -m aiops.kanban.cli move M1-03 done --handoff "..."
    python -m aiops.kanban.cli docs sync|check       # 문서 자동 연동

actor 기본값: $KANBAN_ACTOR 또는 "claude-code". 보드 위치: $KANBAN_ROOT 또는 ./tracking
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from aiops.kanban.board import KanbanBoard
from aiops.kanban.docsync import DocSync, load_config
from aiops.kanban.models import CardType, Column, LogKind, Priority
from aiops.kanban.policy import BoardPolicy, PolicyViolation
from aiops.kanban.stores import FileBoardStore


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def open_board(root: Path) -> tuple[KanbanBoard, dict]:
    tracking = Path(os.environ.get("KANBAN_ROOT", root / "tracking"))
    config = load_config(tracking)
    policy = BoardPolicy(**config.get("policy", {}))
    board = KanbanBoard(
        FileBoardStore(tracking),
        config.get("board_id", "dev"),
        policy=policy,
        prefix=config.get("default_prefix", "T"),
    )
    return board, config


def _commits_for(root: Path, card_id: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", "--fixed-strings", f"--grep=[{card_id}]", "-n", "10"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line for line in out.stdout.splitlines() if line]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


async def cmd_brief(board: KanbanBoard, args) -> None:
    """메모리 없는 새 세션이 가장 먼저 읽는 요약."""
    cards = await board.cards()
    work = [c for c in cards if c.type != CardType.EPIC]
    print("## 📋 칸반 보드 브리핑 (tracking/) — 작업 전 확인, 규칙은 CLAUDE.md")
    counts = ", ".join(f"{col}={sum(1 for c in work if c.column == col)}" for col in Column)
    print(f"- 카드 현황: {counts}")
    for title, col in [
        ("🔄 진행 중 (이어받을 작업)", Column.IN_PROGRESS),
        ("⛔ 막힘", Column.BLOCKED),
        ("👀 검토 대기", Column.REVIEW),
    ]:
        items = [c for c in work if c.column == col]
        if items:
            print(f"\n### {title}")
            for c in items:
                memo = c.blocked_reason if col == Column.BLOCKED else c.handoff
                if col == Column.IN_PROGRESS:  # 진행 중이면 가장 최근 기록이 더 유용하다
                    notes = [e for e in c.log if e.kind in ("note", "decision", "handoff")]
                    memo = f"최근 기록: {notes[-1].message}" if notes else memo
                print(
                    f"- {c.id} [{c.assignee or '-'}] {c.title}\n  ↳ {(memo or '(메모 없음)')[:200]}"
                )
    ready = sorted((c for c in work if c.column == Column.READY), key=lambda c: (c.priority, c.id))[
        : args.limit
    ]
    if ready:
        print("\n### 🟦 다음 착수 후보 (READY, 우선순위순)")
        for c in ready:
            print(f"- {c.id} ({c.priority}) {c.title}")
    print(
        "\n자세히: `python -m aiops.kanban.cli show <ID>` · 착수: `claim <ID>` · "
        "완료: `move <ID> done --handoff ...`"
    )


async def cmd_list(board: KanbanBoard, args) -> None:
    for c in await board.cards():
        if args.column and c.column != args.column:
            continue
        print(f"{c.column:12s} {c.id:10s} {c.priority} {c.assignee or '-':12s} {c.title}")


async def run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="kanban", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--actor", default=os.environ.get("KANBAN_ACTOR", "claude-code"))
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("brief")
    b.add_argument("--limit", type=int, default=5)
    ls = sub.add_parser("list")
    ls.add_argument("--column", choices=[c.value for c in Column])
    sub.add_parser("metrics")

    sh = sub.add_parser("show")
    sh.add_argument("id")

    a = sub.add_parser("add")
    a.add_argument("title")
    a.add_argument("--id")
    a.add_argument("--prefix")
    a.add_argument("--desc", default="")
    a.add_argument("--type", choices=[t.value for t in CardType], default="task")
    a.add_argument("--priority", choices=[x.value for x in Priority], default="p2")
    a.add_argument("--column", choices=["backlog", "ready", "blocked"], default="backlog")
    a.add_argument("--parent")
    a.add_argument("--depends", nargs="*", default=[])
    a.add_argument("--cap", nargs="*", default=[])
    a.add_argument("--label", nargs="*", default=[])
    a.add_argument("--ref", nargs="*", default=[])
    a.add_argument("--accept", nargs="*", default=[])

    e = sub.add_parser("edit")
    e.add_argument("id")
    e.add_argument("--title")
    e.add_argument("--desc")
    e.add_argument("--priority", choices=[x.value for x in Priority])
    e.add_argument("--add-ref", nargs="*", default=[])
    e.add_argument("--add-label", nargs="*", default=[])
    e.add_argument("--accept", nargs="*")

    c = sub.add_parser("claim")
    c.add_argument("id")
    n = sub.add_parser("next")
    n.add_argument("--cap", nargs="*", default=[])

    m = sub.add_parser("move")
    m.add_argument("id")
    m.add_argument("to", choices=[x.value for x in Column])
    m.add_argument("--handoff", default="")
    m.add_argument("--reason", default="")

    nt = sub.add_parser("note")
    nt.add_argument("id")
    nt.add_argument("message")
    nt.add_argument("--decision", action="store_true")

    ck = sub.add_parser("check")
    ck.add_argument("id")
    ck.add_argument("index", type=int, nargs="+")
    ck.add_argument("--undo", action="store_true")

    o = sub.add_parser("output")
    o.add_argument("id")
    o.add_argument("key")
    o.add_argument("value")

    d = sub.add_parser("docs")
    d.add_argument("action", choices=["sync", "check"])
    d.add_argument("--quiet", action="store_true")
    d.add_argument("--porcelain", action="store_true", help="변경된 파일 경로만 한 줄씩 출력")

    args = p.parse_args(argv)
    root = _repo_root()
    board, config = open_board(root)
    actor = args.actor

    try:
        match args.cmd:
            case "brief":
                await cmd_brief(board, args)
            case "list":
                await cmd_list(board, args)
            case "metrics":
                print((await board.metrics()).model_dump_json(indent=2))
            case "show":
                print(await board.briefing(args.id, commits=_commits_for(root, args.id)))
            case "add":
                if args.prefix:
                    board.prefix = args.prefix
                card = await board.create(
                    args.title,
                    actor=actor,
                    id=args.id,
                    description=args.desc,
                    type=CardType(args.type),
                    priority=Priority(args.priority),
                    column=Column(args.column),
                    labels=args.label,
                    refs=args.ref,
                    acceptance=args.accept,
                    capabilities=args.cap,
                    parent_id=args.parent,
                    depends_on=args.depends,
                )
                print(card.id)
            case "edit":
                cur = await board.get(args.id)
                fields = {}
                if args.title:
                    fields["title"] = args.title
                if args.desc is not None:
                    fields["description"] = args.desc
                if args.priority:
                    fields["priority"] = Priority(args.priority)
                if args.add_ref:
                    fields["refs"] = cur.refs + [r for r in args.add_ref if r not in cur.refs]
                if args.add_label:
                    fields["labels"] = cur.labels + [
                        x for x in args.add_label if x not in cur.labels
                    ]
                if args.accept is not None:
                    fields["acceptance"] = args.accept
                await board.edit(args.id, actor, **fields)
            case "claim":
                card = await board.claim(args.id, actor)
                print(await board.briefing(card.id, commits=_commits_for(root, card.id)))
            case "next":
                card = await board.claim_next(actor, set(args.cap))
                print(await board.briefing(card.id) if card else "당겨갈 READY 카드가 없습니다")
            case "move":
                card = await board.move(
                    args.id, Column(args.to), actor, handoff=args.handoff, reason=args.reason
                )
                print(f"{card.id} → {card.column}")
            case "note":
                kind = LogKind.DECISION if args.decision else LogKind.NOTE
                await board.note(args.id, actor, args.message, kind)
            case "check":
                for i in args.index:
                    await board.check(args.id, actor, i, done=not args.undo)
            case "output":
                await board.set_output(args.id, actor, args.key, args.value)
            case "docs":
                sync = DocSync(root, await board.cards(), config)
                result = sync.run(write=args.action == "sync")
                for prob in result.problems:
                    print(f"✘ {prob}", file=sys.stderr)
                if args.action == "check" and result.changed:
                    print(
                        "✘ 문서가 보드와 불일치합니다 — `make docs` 실행 후 커밋하세요: "
                        + ", ".join(result.changed),
                        file=sys.stderr,
                    )
                    return 1
                if args.action == "sync" and args.porcelain:
                    print("\n".join(result.changed))
                elif args.action == "sync" and result.changed and not args.quiet:
                    print("문서 갱신: " + ", ".join(result.changed))
                return 1 if result.problems else 0
    except PolicyViolation as exc:
        print(f"✘ 정책 위반: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"✘ {exc}", file=sys.stderr)
        return 2
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
