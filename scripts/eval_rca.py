"""RCA 원인 후보 랭킹 평가 (M1-08).

python scripts/eval_rca.py              # 결정적 랭킹 Top-1/Top-3
python scripts/eval_rca.py --seeds 50   # 노이즈 시드별 안정성
"""

import argparse
import asyncio
from pathlib import Path

from aiops.evals.rca import evaluate_rca, load_scenarios

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1)
    args = ap.parse_args()
    scenarios = load_scenarios(ROOT / "data/eval/rca_scenarios.json")
    reports = [await evaluate_rca(scenarios, seed=s) for s in range(7, 7 + args.seeds)]
    for o in reports[0].outcomes:
        print(f"{o.id:22s} hit@{o.hit_rank}  {o.top}")
    t1 = [r.top1 for r in reports]
    t3 = [r.top3 for r in reports]
    print(
        f"\n시나리오 {len(scenarios)}개 × 시드 {args.seeds}: "
        f"Top-1 평균 {sum(t1) / len(t1):.3f} (최저 {min(t1)}), "
        f"Top-3 평균 {sum(t3) / len(t3):.3f} (최저 {min(t3)})"
    )


if __name__ == "__main__":
    asyncio.run(main())
