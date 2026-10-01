"""LLM 의사결정용 표준 fact sheet (M2-06 / 3.5).

원시 시계열(점 수백 개)을 LLM 에 그대로 주면 토큰 낭비 + 숫자 환각 위험.
코드가 계산한 '사실'만 고정 스키마로 압축해 준다:
  메트릭별 요약 · 변화점(CUSUM: 언제부터 어느 방향으로) · 함께 움직인 메트릭(상관)
  · 로그 템플릿 · 변경 이력
"""

import json
from datetime import datetime, timedelta
from itertools import combinations

import numpy as np
from pydantic import BaseModel, Field

from aiops.analytics.anomaly.ensemble import default_detector
from aiops.analytics.changepoint import cusum
from aiops.analytics.insights import summarize_series
from aiops.analytics.logs import summarize_logs
from aiops.domain.models import utcnow
from aiops.integrations.base import OpsSource

METRICS = ["cpu_usage", "memory_usage", "latency_p95_ms", "error_rate"]


class MetricFact(BaseModel):
    metric: str
    last: float
    mean: float
    p95: float
    trend_change_pct: float
    anomaly_count: int
    change_min_ago: float | None = None  # 가장 최근 변화점이 시작된 시점 (분 전)
    change_direction: str | None = None


class Correlation(BaseModel):
    a: str
    b: str
    r: float  # 1차 차분의 피어슨 상관 — 추세가 아니라 '함께 움직임'을 본다


class FactSheet(BaseModel):
    service: str
    window_minutes: int
    generated_at: datetime = Field(default_factory=utcnow)
    metrics: list[MetricFact]
    correlated: list[Correlation] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    log_templates: list[str] = Field(default_factory=list)

    def to_prompt(self) -> str:
        """LLM 입력용 압축 텍스트 (JSON 보다 짧고 읽기 쉬운 고정 형식)."""
        lines = [f"[{self.service}] 최근 {self.window_minutes}분"]
        for m in self.metrics:
            cp = (
                f", {m.change_min_ago:.0f}분 전부터 {m.change_direction}"
                if m.change_min_ago is not None
                else ""
            )
            lines.append(
                f"- {m.metric}: last={m.last} mean={m.mean} p95={m.p95} "
                f"추세={m.trend_change_pct:+.0f}% 이상={m.anomaly_count}{cp}"
            )
        if self.correlated:
            lines.append(
                "- 함께 움직임: " + ", ".join(f"{c.a}~{c.b}(r={c.r})" for c in self.correlated)
            )
        if self.changes:
            lines.append("- 변경: " + "; ".join(self.changes))
        if self.log_templates:
            lines.append("- 로그: " + "; ".join(self.log_templates[:5]))
        return "\n".join(lines)


def correlate_metrics(
    series: dict[str, list[float]], min_r: float = 0.6, window: int = 30
) -> list[Correlation]:
    out = []
    for a, b in combinations(sorted(series), 2):
        xa, xb = np.diff(series[a][-window:]), np.diff(series[b][-window:])
        n = min(len(xa), len(xb))
        if n < 5 or np.std(xa[:n]) == 0 or np.std(xb[:n]) == 0:
            continue
        r = float(np.corrcoef(xa[:n], xb[:n])[0, 1])
        if abs(r) >= min_r:
            out.append(Correlation(a=a, b=b, r=round(r, 2)))
    return sorted(out, key=lambda c: -abs(c.r))


async def build_fact_sheet(
    source: OpsSource, service: str, window_min: int = 60, step_s: int = 60
) -> tuple[FactSheet, dict[str, list[float]]]:
    """fact sheet 와 (토큰 비교용) 원시 시계열을 함께 반환."""
    end = utcnow()
    start = end - timedelta(minutes=window_min)
    raw: dict[str, list[float]] = {}
    facts = []
    for metric in METRICS:
        series = await source.query_range(service, metric, start, end, step_s)
        values = series.values
        raw[metric] = values
        if not values:
            continue
        s = summarize_series(values, default_detector().detect(values))
        # cooldown 을 짧게: 앞선 완만한 변화(계절성 등)가 뒤의 진짜 변화를 가리지 않도록
        cps = cusum(values, h=12.0, baseline=min(60, max(10, len(values) // 3)), cooldown=5)
        # 기준선 대비 20% 미만 변화는 보고하지 않는다 (계절성 물결·잡음을 '변화'로 오해하지 않게)
        cps = [c for c in cps if abs(c.relative) >= 0.2]
        last_cp = cps[-1] if cps else None
        facts.append(
            MetricFact(
                metric=metric,
                last=s["last"],
                mean=s["mean"],
                p95=s["p95"],
                trend_change_pct=s["trend_change_pct"],
                anomaly_count=s["anomaly_count"],
                change_min_ago=(len(values) - last_cp.start) * step_s / 60 if last_cp else None,
                change_direction=last_cp.direction if last_cp else None,
            )
        )
    events = await source.list_events(end - timedelta(hours=2), end, service)
    logs = await source.search(service, "error", start, end, limit=500)
    sheet = FactSheet(
        service=service,
        window_minutes=window_min,
        metrics=facts,
        correlated=correlate_metrics({k: v for k, v in raw.items() if v}),
        changes=[
            f"{(end - e.timestamp).total_seconds() / 60:.0f}분 전 {e.type}: {e.message}"
            for e in events
            if e.type in ("deploy", "config", "rollback")
        ],
        log_templates=summarize_logs([r["message"] for r in logs], k=5),
    )
    return sheet, raw


def compression(sheet: FactSheet, raw: dict[str, list[float]]) -> dict[str, float]:
    """원시 시계열(JSON) 대비 fact sheet 크기. 문자 수 기반 토큰 근사(실 토크나이저 아님)."""
    raw_text = json.dumps({k: [round(v, 3) for v in vs] for k, vs in raw.items()})
    sheet_text = sheet.to_prompt()
    return {
        "raw_chars": len(raw_text),
        "sheet_chars": len(sheet_text),
        "reduction_pct": round(100 * (1 - len(sheet_text) / max(len(raw_text), 1)), 1),
    }
