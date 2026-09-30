"""AI Agent 의사결정을 위한 데이터 분석 (3.5).

원시 시계열을 그대로 LLM 에 넣으면 토큰 낭비 + 환각 위험이 크다.
→ 수치 분석은 코드가 하고, LLM 에는 압축된 '사실 요약(fact sheet)'만 전달한다.
"""

from collections.abc import Sequence

import numpy as np

from aiops.analytics.anomaly.base import Anomaly


def summarize_series(values: Sequence[float], anomalies: list[Anomaly] | None = None) -> dict:
    x = np.asarray(values, dtype=float)
    if len(x) == 0:
        return {"count": 0}
    half = len(x) // 2 or 1
    before, after = float(np.mean(x[:half])), float(np.mean(x[half:]))
    return {
        "count": int(len(x)),
        "last": round(float(x[-1]), 3),
        "mean": round(float(np.mean(x)), 3),
        "p95": round(float(np.percentile(x, 95)), 3),
        "min": round(float(np.min(x)), 3),
        "max": round(float(np.max(x)), 3),
        "trend_change_pct": round((after - before) / (abs(before) or 1e-9) * 100, 1),
        "anomaly_count": len(anomalies or []),
        "first_anomaly_index": anomalies[0].index if anomalies else None,
    }
