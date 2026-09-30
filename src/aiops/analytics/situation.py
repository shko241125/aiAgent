"""상황 인식(Situation Awareness) (3.2).

Endsley 모델의 3단계를 코드 구조에 대응시킨다.
  Level 1 Perception    : 신호 수집 — 이상 탐지 결과, 이벤트, 변경 이력  (SignalSet)
  Level 2 Comprehension : 의미 해석 — 신호들을 가중 결합해 현재 위험도 산정 (assess)
  Level 3 Projection    : 미래 예측 — 추세 기반 임계치 도달 위험 반영       (prediction.py)

현재는 규칙·가중치 기반. TODO(3.2): 과거 인시던트 데이터로 가중치 학습(로지스틱 회귀/GBM),
서비스 토폴로지 기반 영향 전파(그래프 모델) 반영.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from aiops.analytics.anomaly.base import Anomaly


class SituationLevel(StrEnum):
    NORMAL = "normal"
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"


class SignalSet(BaseModel):
    service: str
    anomalies: dict[str, list[Anomaly]] = Field(default_factory=dict)  # metric -> anomalies
    series_length: int = 1
    error_events: int = 0
    recent_changes: int = 0  # 최근 배포/설정 변경 수
    forecast_risk: float = 0.0  # prediction.RiskForecast.risk_score 최대값
    downstream_impacted: int = 0


class SituationAssessment(BaseModel):
    service: str
    level: SituationLevel
    risk_score: float  # 0~100
    factors: list[str]


# 메트릭 중요도 가중치 — 운영 정책에 맞게 조정 (TODO: 설정/DB 로 외부화)
METRIC_WEIGHTS = {"error_rate": 1.0, "latency_p95_ms": 0.8, "cpu_usage": 0.5, "memory_usage": 0.5}


def assess(signals: SignalSet) -> SituationAssessment:
    score = 0.0
    factors: list[str] = []
    for metric, anomalies in signals.anomalies.items():
        if not anomalies:
            continue
        w = METRIC_WEIGHTS.get(metric, 0.4)
        ratio = len(anomalies) / max(signals.series_length, 1)
        max_score = max(a.score for a in anomalies)
        contrib = w * (20 + min(max_score, 10) * 2 + ratio * 50)
        score += contrib
        factors.append(f"{metric}: {len(anomalies)} anomalies (max score {max_score:.1f})")
    if signals.error_events:
        score += min(signals.error_events, 10) * 2
        factors.append(f"{signals.error_events} error events")
    if signals.recent_changes and score > 0:
        score *= 1.2  # 변경 직후 이상 = 변경이 원인일 가능성 ↑
        factors.append(f"{signals.recent_changes} recent change(s) correlated")
    if signals.forecast_risk:
        score += signals.forecast_risk * 20
        factors.append(f"forecast risk {signals.forecast_risk:.2f}")
    if signals.downstream_impacted:
        score += signals.downstream_impacted * 5
        factors.append(f"{signals.downstream_impacted} dependent services")

    score = min(score, 100.0)
    if score >= 70:
        level = SituationLevel.CRITICAL
    elif score >= 40:
        level = SituationLevel.WARNING
    elif score >= 15:
        level = SituationLevel.WATCH
    else:
        level = SituationLevel.NORMAL
    return SituationAssessment(
        service=signals.service, level=level, risk_score=round(score, 1), factors=factors
    )
