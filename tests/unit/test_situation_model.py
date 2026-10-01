"""M2-05 학습형 상황 인식 · 영향 전파."""

import numpy as np

from aiops.analytics.situation_model import LogisticModel, level_from_probability, propagate_risk
from aiops.evals.situations import evaluate_situations


def test_propagation_reaches_upstream_with_decay():
    downstream = {"gw": ["order"], "order": ["payment", "db"], "payment": ["pay-db"]}
    eff = propagate_risk(
        {"gw": 0.0, "order": 0.1, "payment": 0.0, "pay-db": 1.0, "db": 0.0}, downstream, decay=0.5
    )
    assert eff == {"gw": 0.12, "order": 0.25, "payment": 0.5, "pay-db": 1.0, "db": 0.0}


def test_logistic_model_learns_and_explains():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 2))
    y = (X[:, 0] > 0).astype(float)  # 첫 특징만 의미 있음
    m = LogisticModel.fit(X, y)
    m.features = ["signal", "noise"]
    assert m.predict_proba(np.array([2.0, 0.0])) > 0.9
    assert m.predict_proba(np.array([-2.0, 0.0])) < 0.1
    assert m.explain(np.array([2.0, 0.0]), top_k=1)[0][0] == "signal"
    assert level_from_probability(0.9) == "critical" and level_from_probability(0.1) == "normal"


async def test_learned_beats_rules_on_held_out_situations():
    """기준선 (합성 상황). 실 알람 이력 라벨로 재검증 필요."""
    r = await evaluate_situations(n_train=12, n_test=8)
    assert r.learned.f1 >= r.rule.f1
    assert r.learned.fpr <= 0.05 and r.learned.recall >= 0.9
    weights = dict(zip(r.model.features, r.model.weights, strict=True))
    assert weights["recent_change"] > 0  # 데이터 편향으로 '배포=정상'을 배우지 않았는지
