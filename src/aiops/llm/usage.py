"""LLM 사용량 집계 (M1-02 / 4.1).

- 프로바이더마다 다른 usage 필드를 {input_tokens, output_tokens} 로 정규화 (normalize_usage)
- 어떤 에이전트가 호출했는지는 contextvar 로 전달 → LLM 인터페이스를 바꾸지 않고 귀속
- 단가는 설정(AIOPS_LLM_PRICES)으로 주입. 모르면 비용은 None (추측 단가를 하드코딩하지 않는다)
"""

import contextvars
import statistics
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel

current_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_agent", default=None
)


def normalize_usage(raw: dict[str, Any] | None) -> dict[str, int]:
    raw = raw or {}
    inp = raw.get("input_tokens", raw.get("prompt_tokens", raw.get("promptTokenCount", 0)))
    out = raw.get("output_tokens", raw.get("completion_tokens", raw.get("candidatesTokenCount", 0)))
    return {"input_tokens": int(inp or 0), "output_tokens": int(out or 0)}


class UsageRecord(BaseModel):
    provider: str
    model: str
    agent: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float
    ok: bool


class UsageGroup(BaseModel):
    key: str
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    avg_latency_ms: float
    p95_latency_ms: float
    cost_usd: float | None


class UsageTracker:
    def __init__(self, prices: dict[str, tuple[float, float]] | None = None, max_records=10000):
        self.prices = prices or {}  # model -> (USD / 1M input tokens, USD / 1M output tokens)
        self.max_records = max_records
        self.records: list[UsageRecord] = []

    def record(self, rec: UsageRecord) -> None:
        self.records.append(rec)
        if len(self.records) > self.max_records:
            del self.records[: len(self.records) - self.max_records]

    def _cost(self, recs: list[UsageRecord]) -> float | None:
        total = 0.0
        for r in recs:
            if r.model not in self.prices:
                return None
            pin, pout = self.prices[r.model]
            total += r.input_tokens / 1e6 * pin + r.output_tokens / 1e6 * pout
        return round(total, 6)

    def summary(self, by: Literal["provider", "model", "agent"] = "provider") -> list[UsageGroup]:
        groups: dict[str, list[UsageRecord]] = defaultdict(list)
        for r in self.records:
            groups[str(getattr(r, by) or "-")].append(r)
        out = []
        for key, recs in sorted(groups.items()):
            lat = sorted(r.latency_ms for r in recs)
            out.append(
                UsageGroup(
                    key=key,
                    calls=len(recs),
                    errors=sum(1 for r in recs if not r.ok),
                    input_tokens=sum(r.input_tokens for r in recs),
                    output_tokens=sum(r.output_tokens for r in recs),
                    avg_latency_ms=round(statistics.fmean(lat), 1),
                    p95_latency_ms=round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1),
                    cost_usd=self._cost(recs),
                )
            )
        return out
