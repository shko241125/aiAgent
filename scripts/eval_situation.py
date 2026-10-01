"""장애 판정 평가 + 상황 인식 모델 학습 (M2-05 / M2-07).

python scripts/eval_situation.py            # 규칙 vs 학습 모델 비교, 트리아지 임계치 스윕
python scripts/eval_situation.py --save     # 학습 모델을 data/models/situation_lr.json 에 저장
"""

import argparse
import asyncio
from pathlib import Path

from aiops.evals.situations import evaluate_situations

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "data/models/situation_lr.json"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    r = await evaluate_situations()
    print(f"학습 {r.train_size} / 평가(보류) {r.test_size} 상황 — 장애 판정 is_incident")
    for s in (r.rule, r.learned):
        print(f"  {s.name:8s} P={s.precision:.3f} R={s.recall:.3f} FPR={s.fpr:.3f} F1={s.f1:.3f}")
    print(
        "계수(표준화 특징):",
        {f: round(w, 2) for f, w in zip(r.model.features, r.model.weights, strict=True)},
    )
    print("트리아지(p<임계치면 LLM 생략) — 임계치: 음성 생략률 / 놓친 양성")
    for th, skip, missed in r.triage_sweep:
        print(f"  {th:<5} {skip:.2f} / {missed}")
    if r.errors:
        print("오판:", r.errors)
    if args.save:
        await asyncio.to_thread(r.model.save, MODEL_PATH)
        print(f"저장: {MODEL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
