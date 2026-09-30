from pathlib import Path

from aiops.evals.prompts import load_prompt_scenarios, run_suite
from aiops.evals.rca import load_scenarios
from aiops.main import create_app  # noqa: F401 - 앱 조립 경로와 동일하게 import 검증

ROOT = Path(__file__).resolve().parents[2]


async def test_scripted_prompt_suite_passes(settings):
    """프롬프트 렌더링·컨텍스트 주입·파싱·스키마 경로 회귀 검사 (M1-07)."""
    from aiops.core.container import build_platform

    platform = await build_platform(
        settings.model_copy(update={"knowledge_dir": ROOT / "data/knowledge"})
    )
    try:
        report = await run_suite(
            platform,
            load_prompt_scenarios(ROOT / "data/eval/prompt_scenarios.json"),
            {s.id: s for s in load_scenarios(ROOT / "data/eval/rca_scenarios.json")},
        )
    finally:
        await platform.aclose()
    failed = {r.id: [c for c in r.checks if not c.ok] for r in report.results if not r.passed}
    assert report.passed == report.total, failed
    rca = next(r for r in report.results if r.id == "rca-deploy-pool")
    assert rca.prompt == "rca" and rca.prompt_version == "2"
