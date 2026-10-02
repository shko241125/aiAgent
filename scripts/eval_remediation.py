"""조치 평가 (M3-03): 1차 조치 정확도 · 실행 후 복구율 · 자동화 금지 시나리오의 안전한 위임.

python scripts/eval_remediation.py [--seeds 5]
"""

import argparse
import asyncio
from pathlib import Path

from aiops.evals.rca import load_scenarios
from aiops.evals.remediation import evaluate_remediation

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1)
    args = ap.parse_args()
    scenarios = load_scenarios(ROOT / "data/eval/rca_scenarios.json")
    reports = [await evaluate_remediation(scenarios, seed=s) for s in range(7, 7 + args.seeds)]
    for c in reports[0].cases:
        mark = "✔" if c.correct_plan else "✘"
        print(f"{mark} {c.id:22s} 정답={c.expected:9s} 계획={c.planned!s:9s} 결과={c.status}")
    for name in ("plan_accuracy", "recovery_rate", "safe_manual"):
        vals = [getattr(r, name) for r in reports]
        print(f"{name:14s} 평균 {sum(vals) / len(vals):.3f} (최저 {min(vals)})")


if __name__ == "__main__":
    asyncio.run(main())
