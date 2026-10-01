"""학습형 상황 인식 (M2-05 / 3.2) — 로지스틱 회귀 + 토폴로지 영향 전파.

왜 로지스틱 회귀인가: 계수가 곧 설명이다. "이 알람이 CRITICAL 인 이유 = error_rate 이상(+2.1),
직전 배포(+0.9) …" 처럼 운영자에게 근거를 보여줄 수 있다. 블랙박스 모델보다 신뢰를 얻기 쉽다.
numpy 로 직접 구현 (경사하강 + L2 규제 + 표준화) — 의존성 없이 학습·저장·재현 가능.
"""

import json
import math
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from aiops.analytics.anomaly.base import Anomaly
from aiops.analytics.rca import RESOURCE_METRICS, ServiceEvidence, match_signatures
from aiops.analytics.situation import SignalSet, SituationAssessment, SituationLevel

FEATURES = [
    "error_score",  # error_rate 이상 최대 점수 (log1p)
    "latency_score",  # latency 이상 최대 점수 (log1p)
    "resource_score",  # cpu/memory 이상 최대 점수 (log1p)
    "anomalous_metrics",  # 지속 이상 메트릭 수
    "ongoing_minutes",  # 이상 지속 시간 (log1p 분)
    "recent_change",  # 범위 내 최근 배포/설정 변경 유무
    "log_signatures",  # 알려진 장애 로그 시그니처 수
    "downstream_anomalous",  # 이상 상태인 하위 의존 서비스 수
]


def featurize(evidence: list[ServiceEvidence]) -> np.ndarray:
    """RCAAnalyzer.collect() 결과(알람 서비스 + 하위 범위) → 특징 벡터."""
    alert = next(e for e in evidence if e.depth == 0)
    m = alert.anomalous_metrics
    res = max((s for k, s in m.items() if k in RESOURCE_METRICS), default=0.0)
    return np.array(
        [
            math.log1p(min(m.get("error_rate", 0.0), 1000)),
            math.log1p(min(m.get("latency_p95_ms", 0.0), 1000)),
            math.log1p(min(res, 1000)),
            float(len(m)),
            math.log1p(alert.onset_min_ago or 0.0),
            float(any(e.changes for e in evidence)),
            float(sum(len(match_signatures(e.log_lines)) for e in evidence)),
            float(sum(1 for e in evidence if e.depth > 0 and e.onset_min_ago is not None)),
        ]
    )


class LogisticModel(BaseModel):
    weights: list[float]
    bias: float
    mean: list[float]
    std: list[float]
    features: list[str] = FEATURES

    @classmethod
    def fit(
        cls, X: np.ndarray, y: np.ndarray, l2: float = 0.01, lr: float = 0.5, epochs: int = 2000
    ) -> "LogisticModel":
        mean, std = X.mean(axis=0), X.std(axis=0)
        std[std == 0] = 1.0
        Z = (X - mean) / std
        w, b = np.zeros(X.shape[1]), 0.0
        for _ in range(epochs):  # 배치 경사하강 (교차 엔트로피 + L2)
            p = 1 / (1 + np.exp(-(Z @ w + b)))
            grad = p - y
            w -= lr * (Z.T @ grad / len(y) + l2 * w)
            b -= lr * float(grad.mean())
        return cls(weights=w.tolist(), bias=b, mean=mean.tolist(), std=std.tolist())

    def _z(self, x: np.ndarray) -> np.ndarray:
        return (x - np.array(self.mean)) / np.array(self.std)

    def predict_proba(self, x: np.ndarray) -> float:
        return float(1 / (1 + np.exp(-(self._z(x) @ np.array(self.weights) + self.bias))))

    def explain(self, x: np.ndarray, top_k: int = 3) -> list[tuple[str, float]]:
        """특징별 기여도(계수 × 표준화 값) 상위 — 판단 근거로 그대로 보여줄 수 있다."""
        contrib = self._z(x) * np.array(self.weights)
        order = np.argsort(-np.abs(contrib))[:top_k]
        return [(self.features[i], round(float(contrib[i]), 2)) for i in order]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "LogisticModel | None":
        return cls.model_validate(json.loads(path.read_text())) if path.exists() else None


def level_from_probability(p: float) -> SituationLevel:
    if p >= 0.85:
        return SituationLevel.CRITICAL
    if p >= 0.5:
        return SituationLevel.WARNING
    if p >= 0.2:
        return SituationLevel.WATCH
    return SituationLevel.NORMAL


def assess_learned(
    model: LogisticModel, service: str, evidence: list[ServiceEvidence]
) -> SituationAssessment:
    x = featurize(evidence)
    p = model.predict_proba(x)
    return SituationAssessment(
        service=service,
        level=level_from_probability(p),
        risk_score=round(p * 100, 1),
        factors=[f"{name} {c:+.2f}" for name, c in model.explain(x)],
    )


def propagate_risk(
    risk: dict[str, float], downstream: dict[str, list[str]], decay: float = 0.6
) -> dict[str, float]:
    """하위 서비스의 위험이 상위로 전파된다: eff(s) = max(own(s), decay × eff(하위)).

    주문 서비스 자체 지표가 멀쩡해도 결제 DB 가 불타면 주문 서비스도 위험하다.
    """
    eff = dict(risk)
    for _ in range(len(risk) + 1):  # 사이클 없는 그래프에서 깊이만큼 반복하면 수렴
        changed = False
        for svc, deps in downstream.items():
            inherited = max((decay * eff.get(d, 0.0) for d in deps), default=0.0)
            if inherited > eff.get(svc, 0.0) + 1e-9:
                eff[svc] = inherited
                changed = True
        if not changed:
            break
    return eff  # 반올림은 표시할 때만 — 비교 전에 반올림하면 임계치 판정이 틀어진다


def rule_signals(evidence: list[ServiceEvidence]) -> SignalSet:
    """규칙 기반 assess() 입력으로 변환 — 같은 근거로 두 방식을 공정하게 비교하기 위함."""
    alert = next(e for e in evidence if e.depth == 0)
    span = int(alert.onset_min_ago or 0)
    return SignalSet(
        service=alert.service,
        anomalies={
            m: [Anomaly(index=0, value=0.0, score=s, method="ev")] * max(span, 1)
            for m, s in alert.anomalous_metrics.items()
        },
        series_length=60,
        error_events=sum(len(match_signatures(e.log_lines)) for e in evidence),
        recent_changes=sum(len(e.changes) for e in evidence),
        downstream_impacted=sum(1 for e in evidence if e.depth > 0 and e.onset_min_ago),
    )
