from pathlib import Path

from aiops.analytics.rca import ServiceEvidence, match_signatures, ongoing_onset, rank_candidates
from aiops.evals.rca import evaluate_rca, load_scenarios

ROOT = Path(__file__).resolve().parents[2]


def test_ongoing_onset_ignores_transient_noise():
    n = 60
    assert ongoing_onset([5, 6, 7], n) is None  # 창 앞부분 일시적 연속 이상
    assert ongoing_onset([5, 6, 7, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59], n) == 50
    assert ongoing_onset([50, 52, 54, 56, 58], n) is None  # 연속 구간 없음


def test_signature_catalog():
    labels = [s[0] for s in match_signatures(["x509: certificate has expired"])]
    assert labels == ["TLS 인증서 만료/검증 실패"]


def test_dependency_that_started_first_outranks_symptom():
    ev = [
        ServiceEvidence(
            service="order-service",
            depth=0,
            onset_min_ago=10,
            anomalous_metrics={"latency_p95_ms": 9},
        ),
        ServiceEvidence(
            service="order-db", depth=1, onset_min_ago=15, anomalous_metrics={"latency_p95_ms": 12}
        ),
    ]
    top = rank_candidates(ev)[0]
    assert (top.service, top.kind) == ("order-db", "dependency")


async def test_rca_scenarios_quality_floor():
    """M1-08 기준선.

    주의: 시나리오가 휴리스틱과 함께 작성돼 과적합 가능 — 실데이터로 재검증 필요.
    """
    scenarios = load_scenarios(ROOT / "data/eval/rca_scenarios.json")
    for seed in (7, 13, 29):
        report = await evaluate_rca(scenarios, seed=seed)
        assert report.top3 == 1.0, report.outcomes
        assert report.top1 >= 0.875, report.outcomes
