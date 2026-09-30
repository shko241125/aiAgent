"""문서 자동 연동 (KB-05) — 보드가 단일 진실 공급원, 문서는 생성물.

생성/검증 대상
1. docs/BOARD.md                     : 전체 파일 생성
2. 모든 docs/**/*.md 의 AUTO 구간      : <!-- AUTO:KIND args --> … <!-- /AUTO -->
   - AUTO:ROADMAP                    : 에픽(R-x.y) 표 — 상태·진행률·열린 TODO 자동 계산
   - AUTO:MILESTONES                 : 마일스톤 진행률
   - AUTO:CARDS label=<label>        : 해당 라벨 카드 목록 (계획 문서 ↔ 이슈 연동)
3. 검증: TODO(x.y) 가 존재하는 에픽을 가리키는가, 카드 refs 경로가 실제 존재하는가

생성 결과는 카드 데이터만의 함수다(현재 시각 등 비결정 요소 없음) → CI 에서 `check` 로 비교 가능.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from aiops.kanban.models import Card, CardType, Column

AUTO_RE = re.compile(r"(<!-- AUTO:(\w+)([^>]*?)-->)(.*?)(<!-- /AUTO -->)", re.DOTALL)
TODO_RE = re.compile(r"TODO\((\d+\.\d+)\)")
MATURITY = {"minimal": "🟢 최소구현", "skeleton": "🧩 골격", "none": "⬜ 미착수"}
COL_ICON = {
    Column.BACKLOG: "📥",
    Column.READY: "🟦",
    Column.IN_PROGRESS: "🔄",
    Column.REVIEW: "👀",
    Column.BLOCKED: "⛔",
    Column.DONE: "✅",
}
PRIO = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}


@dataclass
class SyncResult:
    changed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _cell(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _natural(card_id: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", card_id)]


class DocSync:
    def __init__(self, repo_root: Path, cards: list[Card], config: dict) -> None:
        self.root = repo_root
        self.cards = sorted(cards, key=lambda c: _natural(c.id))
        self.by_id = {c.id: c for c in self.cards}
        self.config = config
        self.epics = {
            c.id.removeprefix("R-"): c
            for c in self.cards
            if c.type == CardType.EPIC and c.id.startswith("R-")
        }

    # ---- 계산 -----------------------------------------------------------------
    def children(self, card: Card) -> list[Card]:
        return [c for c in self.cards if c.parent_id == card.id]

    def todo_counts(self) -> tuple[dict[str, int], list[str]]:
        counts: dict[str, int] = {}
        problems = []
        for path in sorted((self.root / "src").rglob("*.py")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for rid in TODO_RE.findall(line):
                    counts[rid] = counts.get(rid, 0) + 1
                    if rid not in self.epics:
                        rel = path.relative_to(self.root)
                        problems.append(f"{rel}:{lineno}: TODO({rid}) — 존재하지 않는 로드맵 ID")
        return counts, problems

    def epic_status(self, epic: Card) -> str:
        kids = self.children(epic)
        if epic.column == Column.DONE:
            return "✅ 완료"
        if any(k.column == Column.BLOCKED for k in kids):
            return "⛔ 막힘"
        if any(k.column in (Column.IN_PROGRESS, Column.REVIEW) for k in kids):
            return "🔄 진행중"
        base = MATURITY.get(epic.label_value("maturity") or "none", "⬜ 미착수")
        if kids and all(k.column == Column.DONE for k in kids):
            return f"{base} (+{len(kids)} 완료)"
        return base

    def progress(self, cards: list[Card]) -> str:
        if not cards:
            return "-"
        done = sum(1 for c in cards if c.column == Column.DONE)
        return f"{done}/{len(cards)}"

    # ---- 렌더링 ---------------------------------------------------------------
    def render_roadmap(self) -> str:
        todos, _ = self.todo_counts()
        out = []
        for sec_id, sec_title in self.config.get("sections", {}).items():
            out += [
                f"\n### {sec_id}. {sec_title}\n",
                "| ID | 항목 | 모듈 | 상태 | 하위 카드 | 열린 TODO | 다음 과제 | 완료 기준 |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for rid, epic in sorted(self.epics.items(), key=lambda kv: _natural(kv[0])):
                if rid.split(".")[0] != sec_id:
                    continue
                modules = ", ".join(f"`{r}`" for r in epic.refs if "/" in r or r.endswith(".py"))
                accept = "; ".join(("☑ " if a.done else "") + a.text for a in epic.acceptance)
                out.append(
                    f"| {rid} | {_cell(epic.title, 40)} | {modules} | {self.epic_status(epic)} | "
                    f"{self.progress(self.children(epic))} | {todos.get(rid, 0)} | "
                    f"{_cell(epic.description)} | {_cell(accept)} |"
                )
        return "\n".join(out).strip("\n")

    def render_milestones(self) -> str:
        rows = ["| 단계 | 목표 | 포함 ID | 카드 진행 | 상태 |", "|---|---|---|---|---|"]
        for m in self.config.get("milestones", []):
            cards = [c for c in self.cards if c.label_value("milestone") == m["id"]]
            if m.get("done"):
                status = "✅ 완료"
            elif cards and all(c.column == Column.DONE for c in cards):
                status = "✅ 완료"
            elif any(c.column != Column.BACKLOG and c.column != Column.READY for c in cards):
                status = "🔄 진행중"
            else:
                status = "⬜ 대기"
            rows.append(
                f"| **{m['id']}** | {m['goal']} | {', '.join(m.get('epics', []))} | "
                f"{self.progress(cards)} | {status} |"
            )
        return "\n".join(rows)

    def render_cards(self, label: str) -> str:
        cards = [c for c in self.cards if label in c.labels]
        rows = ["| 카드 | 제목 | 상태 | 담당 | 인계 메모 / 막힌 사유 |", "|---|---|---|---|---|"]
        for c in cards:
            memo = c.blocked_reason if c.column == Column.BLOCKED else c.handoff
            rows.append(
                f"| {c.id} | {_cell(c.title, 50)} | {COL_ICON[c.column]} {c.column} | "
                f"{c.assignee or '-'} | {_cell(memo or '')} |"
            )
        return "\n".join(rows) + f"\n\n진행: {self.progress(cards)}"

    def render_board(self) -> str:
        work = [c for c in self.cards if c.type != CardType.EPIC]
        counts = " · ".join(
            f"{COL_ICON[col]} {col} {sum(1 for c in work if c.column == col)}" for col in Column
        )
        lines = [
            "# 작업 보드 (BOARD)",
            "",
            "> ⚠️ 자동 생성 파일 — 직접 수정하지 마세요. 원본: `tracking/cards/*.json`",
            "> 갱신: `make docs` (편집 시 Claude Code 훅·pre-commit 이 자동 실행)",
            "",
            f"**{counts}**",
        ]
        for col in [
            Column.IN_PROGRESS,
            Column.BLOCKED,
            Column.REVIEW,
            Column.READY,
            Column.BACKLOG,
            Column.DONE,
        ]:
            cards = sorted(
                (c for c in work if c.column == col),
                key=lambda c: (PRIO[c.priority], _natural(c.id)),
            )
            if not cards:
                continue
            lines += [
                "",
                f"## {COL_ICON[col]} {col} ({len(cards)})",
                "",
                "| 카드 | 제목 | 우선 | 담당 | 상위 | 인계 메모 / 막힌 사유 |",
                "|---|---|---|---|---|---|",
            ]
            for c in cards:
                memo = c.blocked_reason if col == Column.BLOCKED else c.handoff
                lines.append(
                    f"| {c.id} | {_cell(c.title, 60)} | {c.priority} | "
                    f"{c.assignee or '-'} | {c.parent_id or '-'} | {_cell(memo or '')} |"
                )
        return "\n".join(lines) + "\n"

    # ---- 동기화 ---------------------------------------------------------------
    def _render_region(self, kind: str, args: str) -> str:
        if kind == "ROADMAP":
            return self.render_roadmap()
        if kind == "MILESTONES":
            return self.render_milestones()
        if kind == "CARDS":
            m = re.search(r"label=(\S+)", args)
            return self.render_cards(m.group(1)) if m else "(label 인자 필요)"
        return f"(알 수 없는 AUTO 종류: {kind})"

    def expected_files(self) -> dict[Path, str]:
        files = {self.root / "docs" / "BOARD.md": self.render_board()}
        for path in sorted((self.root / "docs").rglob("*.md")):
            if path.name == "BOARD.md":
                continue
            text = path.read_text(encoding="utf-8")
            if "<!-- AUTO:" not in text:
                continue

            def sub(m: re.Match) -> str:
                return f"{m.group(1)}\n{self._render_region(m.group(2), m.group(3))}\n{m.group(5)}"

            files[path] = AUTO_RE.sub(sub, text)
        return files

    def validate(self) -> list[str]:
        _, problems = self.todo_counts()
        for c in self.cards:
            for ref in c.refs:
                path = ref.split("::")[0]
                if ("/" in path or path.endswith((".py", ".md"))) and not (
                    self.root / path
                ).exists():
                    problems.append(
                        f"{c.id}: refs 경로가 없습니다 → {ref} (코드 이동/삭제 시 카드도 갱신)"
                    )
            for d in c.depends_on + ([c.parent_id] if c.parent_id else []):
                if d not in self.by_id:
                    problems.append(f"{c.id}: 존재하지 않는 카드 참조 → {d}")
        return problems

    def run(self, write: bool) -> SyncResult:
        result = SyncResult(problems=self.validate())
        for path, content in self.expected_files().items():
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                result.changed.append(str(path.relative_to(self.root)))
                if write:
                    path.write_text(content, encoding="utf-8")
        return result


def load_config(tracking_dir: Path) -> dict:
    p = tracking_dir / "board.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"board_id": "dev"}
