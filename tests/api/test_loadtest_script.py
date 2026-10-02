"""scripts/loadtest.py 가 썩지 않게 — 소량으로 실행해 결과 구조와 SLO 판정을 확인."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


async def test_loadtest_smoke(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("lt", ROOT / "scripts/loadtest.py")
    lt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lt)
    out = tmp_path / "r.json"
    argv = ["lt", "--requests", "60", "--concurrency", "4", "--cache", "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    assert await lt.main() == 0
    r = json.loads(out.read_text())
    assert r["requests"] == 60 and r["error_rate"] == 0
    assert r["slo"]["interactive_p95"]["pass"] and "llm_provider_calls" in r
