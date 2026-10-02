"""장애 예측 평가 + 모델 학습 (M4-06 / 3.4).

python scripts/eval_prediction.py          # 학습 모델 vs 선형 외삽: N분 전 적중률·리드타임·오경보율
python scripts/eval_prediction.py --save   # 학습 모델을 data/models/failure_lr.json 에 저장
"""

import argparse
from pathlib import Path

from aiops.evals.prediction import evaluate_prediction

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "data/models/failure_lr.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    r = evaluate_prediction()
    print(
        f"합성 시계열 학습 {r.train_series} / 평가(보류) {r.test_series} — "
        f"'{r.horizon_min}분 안 장애' 예측, 적중 = 장애 {r.lead_min}분 이상 전 알람"
    )
    print(f"θ={r.theta} (검증 세트에서 오경보율 ≤ 0.1 중 적중률 최대)")
    for s in (r.model_score, r.baseline_score):
        print(
            f"  {s.name:22s} 적중={s.recall_at_lead:.3f} 리드타임 중앙값={s.median_lead_min}분 "
            f"오경보={s.false_alarm_rate:.3f} 너무 이른 알람={s.premature_rate:.3f}"
        )
        print(f"  {'':22s} 종류별: {s.by_kind}")
    print("θ 스윕(검증): (θ, 적중, 오경보)", r.theta_sweep)
    print("계수:", {f: round(w, 2) for f, w in zip(r.model.features, r.model.weights, strict=True)})
    if args.save:
        r.model.save(MODEL_PATH)
        print(f"저장: {MODEL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
