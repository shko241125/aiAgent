from aiops.rag.citations import validate_citations


def test_doc_id_and_numbered_citations():
    answer = (
        "인증서를 갱신합니다 [runbook-tls-certificate-expiry]. "
        "게이트웨이를 재로드합니다 [1].\n근거 없는 주장도 섞여 있습니다.\n"
        "과거에도 있었습니다 [postmortem-없는-문서]."
    )
    r = validate_citations(
        answer, {"runbook-tls-certificate-expiry", "runbook-x"}, numbered=["runbook-x"]
    )
    assert r.valid == ["runbook-tls-certificate-expiry", "runbook-x"]
    assert r.invalid == ["postmortem-없는-문서"]  # 환각 인용
    assert r.sentences == 4 and r.cited_sentences == 2 and r.citation_rate == 0.5


def test_no_answer():
    assert validate_citations("", set()).citation_rate == 0.0
