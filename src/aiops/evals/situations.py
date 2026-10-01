"""장애 판정(is_incident) 평가 데이터 (M2-05 / M2-07).

시뮬레이터로 '정답을 아는 상황'을 만든다. 양성은 실제 장애 패턴, 음성은 **헷갈리는 정상 상황**:
영향 없는 배포, 이미 끝난 일시적 스파이크, 잡음 수준의 미세 상승
— 운영에서 헛알람을 만드는 바로 그 유형.
한계: 합성 분포를 학습·평가하므로 실제 알람 이력 라벨로 재검증해야 한다.
"""

import random

import numpy as np
from pydantic import BaseModel

from aiops.analytics.rca import RCAAnalyzer, ServiceEvidence
from aiops.analytics.situation import SituationLevel, assess
from aiops.analytics.situation_model import LogisticModel, featurize, rule_signals
from aiops.integrations.simulated import (
    TOPOLOGY,
    ChangeEvent,
    Fault,
    FaultScenario,
    SimulatedOpsSource,
)

ALERT_SERVICES = list(TOPOLOGY)
SIGNATURE_LOGS = [
    "HikariPool-1 - Connection is not available, request timed out after 30000ms",
    "java.lang.OutOfMemoryError: Java heap space",
    "x509: certificate has expired or is not yet valid",
]
BENIGN_LOGS = ["GET /health 200 2ms", "Retrying request attempt=1 backoff=100ms"]
POSITIVE = ["errors", "latency", "resource", "dependency"]
NEGATIVE = ["calm", "deploy_only", "transient", "mild"]


class LabeledSituation(BaseModel):
    kind: str
    label: int
    scenario: FaultScenario


def make_situation(kind: str, rng: random.Random, idx: int) -> LabeledSituation:
    svc = rng.choice(ALERT_SERVICES)
    faults, changes, logs = [], [], {}
    u = rng.uniform
    if kind == "errors":
        onset = u(5, 30)
        faults.append(
            Fault(service=svc, metric="error_rate", magnitude=u(5, 20), onset_min_ago=onset)
        )
        if rng.random() < 0.5:
            logs[svc] = [rng.choice(SIGNATURE_LOGS)]
        if rng.random() < 0.5:
            changes.append(ChangeEvent(service=svc, minutes_ago=onset + u(1, 15)))
    elif kind == "latency":
        faults.append(
            Fault(service=svc, metric="latency_p95_ms", magnitude=u(2.5, 5), onset_min_ago=u(5, 30))
        )
    elif kind == "resource":
        onset = u(10, 35)
        faults += [
            Fault(
                service=svc,
                metric=rng.choice(["cpu_usage", "memory_usage"]),
                magnitude=u(1.8, 2.6),
                onset_min_ago=onset,
            ),
            Fault(
                service=svc,
                metric="latency_p95_ms",
                magnitude=u(1.5, 2.5),
                onset_min_ago=onset - u(1, 5),
            ),
        ]
    elif kind == "dependency":
        dep = rng.choice(TOPOLOGY[svc]["downstream"])
        onset = u(10, 30)
        faults += [
            Fault(
                service=dep,
                metric=rng.choice(["error_rate", "latency_p95_ms"]),
                magnitude=u(4, 10),
                onset_min_ago=onset,
            ),
            Fault(
                service=svc,
                metric="latency_p95_ms",
                magnitude=u(1.6, 2.5),
                onset_min_ago=onset - u(1, 4),
            ),
        ]
    elif kind == "deploy_only":
        changes.append(ChangeEvent(service=svc, minutes_ago=u(10, 40)))
        logs[svc] = list(BENIGN_LOGS)
    elif kind == "transient":
        faults.append(
            Fault(
                service=svc,
                metric=rng.choice(["error_rate", "latency_p95_ms"]),
                magnitude=u(3, 6),
                onset_min_ago=u(20, 45),
                duration_min=u(1, 3),
            )
        )
    elif kind == "mild":
        faults.append(
            Fault(
                service=svc,
                metric=rng.choice(["cpu_usage", "latency_p95_ms"]),
                magnitude=u(1.05, 1.15),
                onset_min_ago=u(10, 40),
            )
        )
    # 변경 이벤트를 양성·음성 모두에 비슷한 비율로 섞는다. 한쪽에만 몰리면 모델이
    # "배포 = 정상" 같은 가짜 규칙을 배운다 (첫 실험에서 recent_change 계수가 음수로 나왔던 원인).
    if not changes and kind not in ("deploy_only", "calm") and rng.random() < 0.4:
        onset = max((f.onset_min_ago for f in faults), default=20.0)
        changes.append(ChangeEvent(service=svc, minutes_ago=onset + u(1, 15)))
    return LabeledSituation(
        kind=kind,
        label=int(kind in POSITIVE),
        scenario=FaultScenario(
            id=f"{kind}-{idx}", alert_service=svc, faults=faults, changes=changes, logs=logs
        ),
    )


def generate_situations(n_per_kind: int, seed: int) -> list[LabeledSituation]:
    rng = random.Random(seed)
    return [make_situation(k, rng, i) for i in range(n_per_kind) for k in POSITIVE + NEGATIVE]


async def collect_evidence(s: LabeledSituation, seed: int) -> list[ServiceEvidence]:
    source = SimulatedOpsSource(seed=seed)
    source.apply_scenario(s.scenario)
    return await RCAAnalyzer(source).collect(s.scenario.alert_service)


class BinaryScore(BaseModel):
    name: str
    precision: float
    recall: float
    fpr: float
    f1: float


def binary_score(name: str, y: list[int], pred: list[int]) -> BinaryScore:
    yt, yp = np.array(y), np.array(pred)
    tp = int(((yt == 1) & (yp == 1)).sum())
    fp = int(((yt == 0) & (yp == 1)).sum())
    fn = int(((yt == 1) & (yp == 0)).sum())
    tn = int(((yt == 0) & (yp == 0)).sum())
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return BinaryScore(
        name=name,
        precision=round(p, 3),
        recall=round(r, 3),
        fpr=round(fp / (fp + tn) if fp + tn else 0.0, 3),
        f1=round(2 * p * r / (p + r) if p + r else 0.0, 3),
    )


class SituationEvalReport(BaseModel):
    train_size: int
    test_size: int
    rule: BinaryScore
    learned: BinaryScore
    model: LogisticModel
    triage_skip_rate: float  # p < 임계치 → LLM 생략 가능한 음성 비율
    triage_missed: int  # 그 임계치에서 놓치는 양성 수
    triage_sweep: list[tuple[float, float, int]]  # (임계치, 음성 생략률, 놓친 양성 수)
    errors: list[str]


async def featurized(sits: list[LabeledSituation], seed: int):
    X, y, ev = [], [], []
    for s in sits:
        e = await collect_evidence(s, seed)
        X.append(featurize(e))
        y.append(s.label)
        ev.append(e)
    return np.array(X), np.array(y), ev


async def evaluate_situations(
    n_train: int = 15,
    n_test: int = 10,
    triage: float = 0.1,
    rule_level: SituationLevel = SituationLevel.WARNING,
) -> SituationEvalReport:
    train = generate_situations(n_train, seed=1)
    test = generate_situations(n_test, seed=2)  # 다른 무작위 추출 → 보류(held-out) 평가
    Xtr, ytr, _ = await featurized(train, seed=11)
    Xte, yte, ev_te = await featurized(test, seed=22)
    model = LogisticModel.fit(Xtr, ytr)
    probs = [model.predict_proba(x) for x in Xte]
    order = list(SituationLevel)
    rule_pred = [
        int(order.index(assess(rule_signals(e)).level) >= order.index(rule_level)) for e in ev_te
    ]
    learned_pred = [int(p >= 0.5) for p in probs]
    errors = [
        f"{s.kind}(label={s.label}) p={p:.2f}"
        for s, p, yp in zip(test, probs, learned_pred, strict=True)
        if yp != s.label
    ]
    neg = [p for p, t in zip(probs, yte, strict=True) if t == 0]

    def triage_at(th: float) -> tuple[float, float, int]:
        skip = sum(1 for p in neg if p < th) / max(len(neg), 1)
        missed = sum(1 for p, t in zip(probs, yte, strict=True) if t == 1 and p < th)
        return th, round(skip, 3), missed

    return SituationEvalReport(
        train_size=len(train),
        test_size=len(test),
        rule=binary_score("rule", yte.tolist(), rule_pred),
        learned=binary_score("learned", yte.tolist(), learned_pred),
        model=model,
        triage_skip_rate=round(sum(1 for p in neg if p < triage) / max(len(neg), 1), 3),
        triage_missed=sum(1 for p, t in zip(probs, yte, strict=True) if t == 1 and p < triage),
        triage_sweep=[triage_at(th) for th in (0.01, 0.02, 0.05, 0.1, 0.2)],
        errors=errors,
    )
