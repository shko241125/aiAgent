"""카드 브리핑 (KB-03) — 메모리 없는 에이전트가 작업을 이어받는 유일한 진입점.

"이 문서만 읽고 일을 이어갈 수 있는가?" 가 품질 기준이다.
"""

import json
from datetime import datetime
from typing import Any

from aiops.kanban.models import Card, Column

NEXT_ACTION = {
    Column.BACKLOG: "아직 착수 대상이 아닙니다. 계획을 다듬거나 READY 로 올리세요.",
    Column.READY: "claim 해서 착수하세요. 선행 카드 결과와 이전 인계 메모를 먼저 확인하세요.",
    Column.IN_PROGRESS: (
        "작업을 계속하세요. 의미 있는 진척·결정은 note/decision 으로 남기고, "
        "결과물은 outputs 에 기록하세요. "
        "끝나면 handoff 메모와 함께 REVIEW 또는 DONE 으로 옮기세요 (DoD 체크 필수)."
    ),
    Column.REVIEW: (
        "결과물을 DoD 기준으로 검토하고 DONE 으로 옮기거나, 문제가 있으면 claim 해서 재작업하세요."
    ),
    Column.BLOCKED: "막힌 사유를 해소할 수 있는지 확인하세요. 해소되면 READY 로 옮기세요.",
    Column.DONE: "완료된 카드입니다. 재작업이 필요하면 READY 로 reopen 하세요.",
}


def _short(value: Any, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " …(생략)"


def render_briefing(
    card: Card,
    cards_by_id: dict[str, Card],
    *,
    commits: list[str] | None = None,
    now: datetime | None = None,
    log_limit: int = 12,
) -> str:
    lines = [f"# 작업 카드 {card.id}: {card.title}", ""]
    meta = f"- 상태: **{card.column}** | 유형: {card.type} | 우선순위: {card.priority}"
    meta += f" | 담당: {card.assignee or '-'}"
    lines.append(meta)
    if card.lease_expires_at and now:
        remain = int((card.lease_expires_at - now).total_seconds())
        lines.append(f"- lease 남은 시간: {remain}s")
    if card.blocked_reason:
        lines.append(f"- ⛔ 막힌 사유: {card.blocked_reason}")
    if card.labels:
        lines.append(f"- 라벨: {', '.join(card.labels)}")
    if card.refs:
        lines.append(f"- 관련: {', '.join(card.refs)}")
    if card.capabilities:
        lines.append(f"- 필요 능력: {', '.join(card.capabilities)}")

    if card.description:
        lines += ["", "## 목표", card.description]
    if card.acceptance:
        lines += ["", "## 완료 조건 (DoD)"]
        lines += [
            f"{i}. [{'x' if a.done else ' '}] {a.text}" for i, a in enumerate(card.acceptance)
        ]

    parent = cards_by_id.get(card.parent_id) if card.parent_id else None
    if parent:
        lines += ["", f"## 상위 카드 {parent.id}: {parent.title} ({parent.column})"]
        if parent.description:
            lines.append(_short(parent.description, 400))

    if card.depends_on:
        lines += ["", "## 선행 카드 결과"]
        for dep_id in card.depends_on:
            dep = cards_by_id.get(dep_id)
            if dep is None:
                lines.append(f"- {dep_id}: (없음)")
                continue
            lines.append(f"### {dep.id}: {dep.title} ({dep.column})")
            if dep.handoff:
                lines.append(f"- 인계: {_short(dep.handoff)}")
            for k, v in dep.outputs.items():
                lines.append(f"- outputs.{k}: {_short(v, 400)}")

    lines += ["", "## 이전 담당자 인계 메모 (handoff)", card.handoff or "(없음 — 첫 착수)"]

    if card.outputs:
        lines += ["", "## 카드 공유칠판 (outputs)"]
        lines += [f"- {k}: {_short(v)}" for k, v in card.outputs.items()]

    if card.log:
        lines += ["", f"## 작업 이력 (최근 {min(log_limit, len(card.log))}건)"]
        for e in card.log[-log_limit:]:
            lines.append(f"- {e.ts:%Y-%m-%d %H:%M} [{e.actor}] {e.kind}: {_short(e.message, 300)}")

    if commits:
        lines += ["", "## 관련 커밋"] + [f"- {c}" for c in commits]

    lines += ["", "## 지금 할 일", NEXT_ACTION[card.column]]
    return "\n".join(lines)
