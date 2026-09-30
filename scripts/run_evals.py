"""프롬프트/에이전트 평가 (M1-07).

python scripts/run_evals.py                 # scripted — 배관 회귀 검사 (CI 와 동일)
python scripts/run_evals.py --live          # 실제 모델 (.env 의 AIOPS_LLM_DEFAULT_PROVIDER)
python scripts/run_evals.py --live --out r.json
"""

import argparse
import asyncio
import tempfile
from pathlib import Path

from aiops.core.config import get_settings
from aiops.core.container import build_platform
from aiops.evals.prompts import load_prompt_scenarios, run_suite
from aiops.evals.rca import load_scenarios

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--out")
    args = ap.parse_args()

    settings = get_settings().model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/eval.db",
            "knowledge_dir": ROOT / "data/knowledge",
        }
    )
    if args.live and settings.llm_default_provider == "fake":
        raise SystemExit(
            "--live 는 실제 LLM 설정이 필요합니다 (AIOPS_LLM_DEFAULT_PROVIDER, API 키)"
        )
    platform = await build_platform(settings)
    scenarios = load_prompt_scenarios(ROOT / "data/eval/prompt_scenarios.json")
    if args.only:
        scenarios = [s for s in scenarios if s.id == args.only]
    faults = {s.id: s for s in load_scenarios(ROOT / "data/eval/rca_scenarios.json")}
    report = await run_suite(platform, scenarios, faults, mode="live" if args.live else "scripted")
    await platform.aclose()

    print(f"[{report.mode}] {report.passed}/{report.total} 통과")
    for r in report.results:
        mark = "✔" if r.passed else "✘"
        print(
            f"{mark} {r.id:24s} {r.prompt}@v{r.prompt_version} {r.latency_ms:7.0f}ms "
            f"tokens={r.usage}"
        )
        for c in r.checks:
            if not c.ok:
                print(f"    ✘ {c.type}: {c.detail}")
    if args.out:
        await asyncio.to_thread(
            Path(args.out).write_text, report.model_dump_json(indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
