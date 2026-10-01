"""M2-04 Drain 로그 템플릿 · 알람 압축률."""

from datetime import timedelta

from aiops.analytics.events import alert_compression
from aiops.analytics.logs import DrainParser, grouping_accuracy, preprocess, summarize_logs
from aiops.analytics.logs_synthetic import generate_logs
from aiops.analytics.rca import match_signatures
from aiops.domain.models import OpsEvent, utcnow


def test_drain_grouping_accuracy_floor():
    """기준선. 합성 템플릿은 실제 로그보다 쉽다 (LogPAI 벤치마크 Drain 평균 GA ≈ 0.86)."""
    for seed in range(3):
        lines, labels = generate_logs(n=1500, seed=seed)
        parser = DrainParser()
        assert grouping_accuracy(parser.parse(lines), labels) >= 0.95
        assert len(parser.clusters) <= 10


def test_masking_keeps_signature_tokens():
    tokens = preprocess("x509: expired at 2026-10-01T03:00:00Z code=503 after 1.5 s")
    assert tokens[0] == "x509:" and "code=<*>" in tokens and tokens.count("<*>") == 2


def test_signatures_still_match_on_templates():
    lines = [
        f"HikariPool-1 - Connection is not available, request timed out after {i}ms."
        for i in range(200)
    ]
    [summary] = summarize_logs(lines)
    assert summary.endswith("(×200)")
    assert [s[0] for s in match_signatures([summary])] == ["DB 커넥션 풀 고갈"]


def test_alert_storm_compression():
    t0 = utcnow()
    storm = [
        OpsEvent(
            timestamp=t0 + timedelta(seconds=10 * i), source="p", service=svc, type=f"alert.{kind}"
        )
        for i in range(60)
        for svc, kind in [
            ("order-service", "latency"),
            ("order-service", "errors"),
            ("payment-service", "errors"),
        ][i % 3 : i % 3 + 1]
    ]
    report = alert_compression(storm)
    assert report.raw == 60 and report.clusters == 1 and report.ratio == 60.0
    assert storm[0].attributes == {}  # 입력 이벤트를 변경하지 않는다
