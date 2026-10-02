"""운영 보고서 (M4-07 / 2.5) — 숫자는 코드가 집계, 서술만 LLM.

collect_facts()  : 기간 지표 집계 (인시던트·MTTR·자동 복구·조치·승인·예측 알람, 이전 기간 대비)
render_markdown(): 결정적 표 + 일별 막대(텍스트) + LLM 서술(실패 시 템플릿)
render_chart_svg(): 일별 인시던트 막대 차트 (API 로 내려받는 첨부)
ReportService    : 생성(멱등 id)·저장·발송. 주간 id = weekly-<ISO주> → 재실행해도 한 번만 발송

LLM 서술 속 숫자가 fact sheet 에 없으면 '확인 안 된 수치' 로 표시한다 (인용 검증과 같은 발상).
"""

import json
import logging
import re
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select

from aiops.db.models import ApprovalRow, IncidentEventRow, IncidentRow, OpsEventRow, ReportRow
from aiops.domain.models import utcnow
from aiops.llm.base import ChatMessage
from aiops.llm.usage import current_agent
from aiops.services.prediction import EVENT_TYPE as PREDICTION_EVENT

logger = logging.getLogger(__name__)
SPARK = "▁▂▃▄▅▆▇█"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite 는 tz 를 버린다


class Report(BaseModel):
    id: str
    kind: str
    period_start: datetime
    period_end: datetime
    created_at: datetime
    delivered_at: datetime | None = None
    markdown: str
    facts: dict[str, Any]


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
async def collect_facts(sessionmaker, start: datetime, end: datetime, tz: str = "UTC") -> dict:
    async with sessionmaker() as s:
        incidents = list(
            await s.scalars(
                select(IncidentRow).where(
                    IncidentRow.created_at >= start, IncidentRow.created_at < end
                )
            )
        )
        prev_start = start - (end - start)
        prev_count = len(
            list(
                await s.scalars(
                    select(IncidentRow.id).where(
                        IncidentRow.created_at >= prev_start, IncidentRow.created_at < start
                    )
                )
            )
        )
        tl = list(
            await s.scalars(
                select(IncidentEventRow)
                .where(IncidentEventRow.ts >= prev_start, IncidentEventRow.ts < end)
                .order_by(IncidentEventRow.ts)
            )
        )
        approvals = list(
            await s.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.requested_at >= start, ApprovalRow.requested_at < end
                )
            )
        )
        predictions = [
            e
            for e in await s.scalars(
                select(OpsEventRow).where(
                    OpsEventRow.timestamp >= start - timedelta(hours=1), OpsEventRow.timestamp < end
                )
            )
            if e.type == PREDICTION_EVENT
        ]

    first_seen: dict[str, datetime] = {}
    for e in tl:
        first_seen.setdefault(e.incident_id, _aware(e.ts))

    def mttrs(lo: datetime, hi: datetime) -> tuple[list[float], int]:
        """[lo, hi) 에 RESOLVED 된 인시던트의 MTTR(분) 목록과 그중 자동(orchestrator) 해결 수."""
        out, auto = [], 0
        for e in tl:
            ts = _aware(e.ts)
            if e.kind == "status" and e.data.get("to") == "resolved" and lo <= ts < hi:
                out.append(round((ts - first_seen[e.incident_id]).total_seconds() / 60, 2))
                auto += e.actor == "orchestrator"
        return out, auto

    mttr, auto_resolved = mttrs(start, end)
    prev_mttr, _ = mttrs(prev_start, start)
    in_period = [e for e in tl if start <= _aware(e.ts) < end]
    actions = [e for e in in_period if e.kind == "action"]
    reverts = [e for e in actions if e.message.startswith("되돌림")]
    verifications = [e for e in in_period if e.kind == "verification"]
    decided = [a for a in approvals if a.decided_at]
    decision_min = [
        (_aware(a.decided_at) - _aware(a.requested_at)).total_seconds() / 60 for a in decided
    ]

    zone = ZoneInfo(tz)
    days = []
    d = start
    while d < end:
        days.append(d.astimezone(zone).date().isoformat())
        d += timedelta(days=1)
    daily = dict.fromkeys(days, 0)
    for inc in incidents:
        key = _aware(inc.created_at).astimezone(zone).date().isoformat()
        if key in daily:
            daily[key] += 1

    def count(values) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in values:
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    preceded = sum(
        1
        for inc in incidents
        if any(
            p.service == inc.service
            and _aware(inc.created_at) - timedelta(hours=1)
            <= _aware(p.timestamp)
            <= _aware(inc.created_at)
            for p in predictions
        )
    )

    def avg(xs: list[float]) -> float | None:
        return round(statistics.mean(xs), 1) if xs else None

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": tz},
        "incidents": {
            "total": len(incidents),
            "previous_total": prev_count,
            "by_severity": count(i.severity for i in incidents),
            "by_service": count(i.service for i in incidents),
            "open_now": sum(1 for i in incidents if i.status not in ("resolved", "closed")),
            "daily": daily,
        },
        "mttr_min": {
            "resolved": len(mttr),
            "mean": avg(mttr),
            "p50": round(statistics.median(mttr), 1) if mttr else None,
            "max": max(mttr) if mttr else None,
            "previous_mean": avg(prev_mttr),
            "auto_resolved": auto_resolved,
        },
        "remediation": {
            "actions": len(actions) - len(reverts),
            "succeeded": sum(1 for e in actions if e not in reverts and e.data.get("ok")),
            "recovered": sum(1 for e in verifications if e.data.get("recovered")),
            "not_recovered": sum(1 for e in verifications if e.data.get("recovered") is False),
            "reverted": len(reverts),
        },
        "approvals": {
            "requested": len(approvals),
            "by_status": count(a.status for a in approvals),
            "escalated": sum(1 for a in approvals if a.escalated_at),
            "decision_p50_min": round(statistics.median(decision_min), 1) if decision_min else None,
        },
        "predictions": {
            "alarms": sum(1 for p in predictions if start <= _aware(p.timestamp) < end),
            "incidents_preceded": preceded,
        },
    }


# ---------------------------------------------------------------------------
# 렌더링
# ---------------------------------------------------------------------------
def sparkline(values: list[int]) -> str:
    top = max(values, default=0)
    if top == 0:
        return SPARK[0] * len(values)
    return "".join(SPARK[round(v / top * (len(SPARK) - 1))] for v in values)


def render_chart_svg(daily: dict[str, int], title: str = "일별 인시던트") -> str:
    w, h, pad, top_pad = 560, 200, 32, 28
    n = max(len(daily), 1)
    top = max(daily.values(), default=0) or 1
    bw = (w - 2 * pad) / n
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="{pad}" y="18" font-size="13" fill="#222">{title}</text>',
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#999"/>',
    ]
    for i, (day, v) in enumerate(daily.items()):
        bh = (h - pad - top_pad) * v / top
        x = pad + i * bw + bw * 0.15
        y = h - pad - bh
        cx = x + bw * 0.35
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.7:.1f}" height="{bh:.1f}" '
            'fill="#4a7bd0"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{y - 4:.1f}" text-anchor="middle" fill="#222">{v}</text>'
        )
        parts.append(
            f'<text x="{x + bw * 0.35:.1f}" y="{h - pad + 14}" text-anchor="middle" fill="#555">'
            f"{day[5:]}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _delta(cur: float | None, prev: float | None) -> str:
    if cur is None or prev is None:
        return "-"
    d = round(cur - prev, 1)
    return f"{d:+g}"


def fact_numbers(facts: Any) -> set[str]:
    """fact sheet 안의 모든 수치(여러 표기) — LLM 서술 검증용."""
    out: set[str] = set()
    if isinstance(facts, dict):
        for k, v in facts.items():
            out |= fact_numbers(k) | fact_numbers(v)
    elif isinstance(facts, list | tuple):
        for v in facts:
            out |= fact_numbers(v)
    elif isinstance(facts, bool):
        pass
    elif isinstance(facts, int | float):
        for v in (facts, round(facts, 1), round(facts, 2)):
            out.add(f"{v:g}")
    elif isinstance(facts, str):
        out |= set(re.findall(r"\d+(?:\.\d+)?", facts))
    return out


def unverified_numbers(narrative: str, facts: dict) -> list[str]:
    known = fact_numbers(facts)
    # 백분율 표기(87%)는 비율 값(0.87)이 사실표에 있을 수 있으니 같이 인정
    found = re.findall(r"\d+(?:\.\d+)?", narrative)
    return sorted({n for n in found if n not in known and f"{float(n) / 100:g}" not in known})


def template_narrative(f: dict) -> str:
    inc, m, r = f["incidents"], f["mttr_min"], f["remediation"]
    lines = [
        "### 요약",
        f"- 인시던트 {inc['total']}건 (이전 기간 {inc['previous_total']}건), "
        f"해결 {m['resolved']}건 중 자동 복구 {m['auto_resolved']}건.",
        "### 주요 관찰",
        f"- 조치 {r['actions']}건 중 복구 확인 {r['recovered']}건, 미복구 {r['not_recovered']}건.",
    ]
    if inc["by_service"]:
        svc, n = next(iter(inc["by_service"].items()))
        lines.append(f"- 가장 많은 서비스: {svc} ({n}건).")
    lines += [
        "### 권고 (다음 주 할 일)",
        "- 미복구·되돌림 사례의 원인 분석을 포스트모템으로 남길 것.",
    ]
    return "\n".join(lines)


def render_markdown(report_id: str, f: dict, narrative: str, unverified: list[str]) -> str:
    inc, m, r, a, p = (
        f["incidents"],
        f["mttr_min"],
        f["remediation"],
        f["approvals"],
        f["predictions"],
    )
    period = f["period"]
    daily = inc["daily"]

    def show(v: Any) -> str:
        return "-" if v is None else str(v)

    rows = [
        "| 지표 | 이번 기간 | 이전 기간 | 변화 |",
        "|---|---|---|---|",
        f"| 인시던트 | {inc['total']} | {inc['previous_total']} | "
        f"{_delta(inc['total'], inc['previous_total'])} |",
        f"| MTTR 평균(분) | {show(m['mean'])} | {show(m['previous_mean'])} | "
        f"{_delta(m['mean'], m['previous_mean'])} |",
        f"| 자동 복구 / 해결 | {m['auto_resolved']} / {m['resolved']} | - | - |",
        f"| 예측 알람이 선행한 인시던트 | {p['incidents_preceded']} / {inc['total']} | - | - |",
    ]
    by_svc = ", ".join(f"{k} {v}" for k, v in inc["by_service"].items()) or "-"
    by_sev = ", ".join(f"{k} {v}" for k, v in inc["by_severity"].items()) or "-"
    by_apr = ", ".join(f"{k} {v}" for k, v in a["by_status"].items()) or "-"
    days_text = ", ".join(f"{d[5:]}:{v}" for d, v in daily.items())
    out = [
        f"# 운영 보고서 — {report_id}",
        f"기간: {period['start'][:16]} ~ {period['end'][:16]} "
        f"(UTC, 일별 집계는 {period['timezone']})",
        "",
        "## 한눈에 보기",
        *rows,
        "",
        "## 일별 인시던트",
        f"`{sparkline(list(daily.values()))}`  ({days_text})",
        f"차트: `GET /api/v1/reports/{report_id}/chart.svg`",
        "",
        "## 분포",
        f"- 서비스별: {by_svc}",
        f"- 심각도별: {by_sev}",
        f"- 미해결(현재): {inc['open_now']}건",
        "",
        "## 조치·승인",
        f"- 조치 {r['actions']}건 (실행 성공 {r['succeeded']}, 복구 확인 {r['recovered']}, "
        f"미복구 {r['not_recovered']}, 되돌림 {r['reverted']})",
        f"- 승인 요청 {a['requested']}건 ({by_apr}), 에스컬레이션 {a['escalated']}건, "
        f"결정 소요 중앙값 {show(a['decision_p50_min'])}분",
        f"- 선제 예측 알람 {p['alarms']}건",
        "",
        "## 해석 (LLM 작성 — 수치는 위 표가 기준)",
        narrative.strip(),
    ]
    if unverified:
        out += [
            "",
            f"> ⚠ 사실표에서 확인되지 않은 수치: {', '.join(unverified)} — 위 표를 기준으로 볼 것",
        ]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 서비스
# ---------------------------------------------------------------------------
def weekly_period(now: datetime, tz: str) -> tuple[datetime, datetime, str]:
    """직전 '완결된' 주 (현지 월요일 00:00 ~ 다음 월요일 00:00) 와 그 주의 결정적 id."""
    local = now.astimezone(ZoneInfo(tz))
    this_monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = this_monday - timedelta(days=7)
    year, week, _ = start.isocalendar()
    return start.astimezone(UTC), this_monday.astimezone(UTC), f"weekly-{year}-W{week:02d}"


class ReportService:
    def __init__(self, platform) -> None:
        self.p = platform

    @property
    def tz(self) -> str:
        return self.p.settings.report_timezone

    async def get(self, report_id: str) -> Report | None:
        async with self.p.sessionmaker() as s:
            row = await s.get(ReportRow, report_id)
            return _to_report(row) if row else None

    async def recent(self, limit: int = 20) -> list[Report]:
        async with self.p.sessionmaker() as s:
            rows = await s.scalars(
                select(ReportRow).order_by(ReportRow.created_at.desc()).limit(limit)
            )
            return [_to_report(r) for r in rows]

    async def chart(self, report_id: str) -> str | None:
        async with self.p.sessionmaker() as s:
            row = await s.get(ReportRow, report_id)
            return row.chart_svg if row else None

    async def _narrative(self, facts: dict) -> str:
        prompt = self.p.prompts.render(
            "ops_report",
            period=f"{facts['period']['start'][:10]} ~ {facts['period']['end'][:10]}",
            facts=json.dumps(facts, ensure_ascii=False, indent=1),
        )
        token = current_agent.set("report")
        try:
            resp = await self.p.llm.chat(
                [ChatMessage.system(prompt.system), ChatMessage.user(prompt.user)], temperature=0.0
            )
            if not (resp.content or "").strip():
                raise ValueError("빈 응답")
            return resp.content
        except Exception as exc:  # noqa: BLE001 - LLM 이 없어도 숫자 보고서는 나간다
            logger.warning("보고서 서술 LLM 실패 → 템플릿: %s", exc)
            return template_narrative(facts)
        finally:
            current_agent.reset(token)

    async def generate(self, report_id: str, kind: str, start: datetime, end: datetime) -> Report:
        """멱등: 같은 id 가 있으면 다시 만들지 않는다."""
        if existing := await self.get(report_id):
            return existing
        facts = await collect_facts(self.p.sessionmaker, start, end, self.tz)
        narrative = await self._narrative(facts)
        md = render_markdown(report_id, facts, narrative, unverified_numbers(narrative, facts))
        row = ReportRow(
            id=report_id,
            kind=kind,
            period_start=start,
            period_end=end,
            created_at=utcnow(),
            markdown=md,
            facts=facts,
            chart_svg=render_chart_svg(facts["incidents"]["daily"]),
        )
        async with self.p.sessionmaker() as s:
            if await s.get(ReportRow, report_id):  # 동시 생성 경합
                return await self.get(report_id)
            s.add(row)
            await s.commit()
        return _to_report(row)

    async def deliver(self, report: Report) -> bool:
        """알림 채널로 요약 발송. 이미 발송했으면 False (재시작 후 중복 발송 방지).

        발송 '전에' 표시한다 = 최대 한 번(at-most-once). 보고서는 API 로 언제든 다시 볼 수 있으니
        두 번 가는 것보다 한 번 놓치는 쪽이 낫다 (조치 실행과는 반대의 선택).
        """
        async with self.p.sessionmaker() as s:
            row = await s.get(ReportRow, report.id)
            if row is None or row.delivered_at is not None:
                return False
            row.delivered_at = utcnow()
            await s.commit()
        head = report.markdown.split("## 일별 인시던트")[0].strip()
        await self.p.notifier.info(f"{head}\n\n전체 보고서: GET /api/v1/reports/{report.id}")
        return True

    async def run_weekly(self, now: datetime | None = None) -> Report:
        start, end, rid = weekly_period(now or utcnow(), self.tz)
        report = await self.generate(rid, "weekly", start, end)
        await self.deliver(report)
        return report


def _to_report(r: ReportRow) -> Report:
    return Report(
        id=r.id,
        kind=r.kind,
        period_start=_aware(r.period_start),
        period_end=_aware(r.period_end),
        created_at=_aware(r.created_at),
        delivered_at=_aware(r.delivered_at) if r.delivered_at else None,
        markdown=r.markdown,
        facts=r.facts,
    )
