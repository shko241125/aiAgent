"""이상 탐지기 비교 (M2-03): 패턴별 이벤트 단위 Precision/Recall/F1·탐지 지연·헛알람 수.

python scripts/eval_anomaly.py [--seeds 5]
"""

import argparse

from aiops.analytics.anomaly.ensemble import EnsembleDetector
from aiops.analytics.anomaly.evaluation import evaluate_detector
from aiops.analytics.anomaly.seasonal import SeasonalDetector
from aiops.analytics.anomaly.statistical import EWMADetector, RobustZScoreDetector
from aiops.analytics.anomaly.synthetic import dataset

DETECTORS = {
    "robust_zscore": lambda s: RobustZScoreDetector(),
    "robust_zscore(t=4)": lambda s: RobustZScoreDetector(threshold=4.0),  # 임계치 효과 분리용
    "ewma": lambda s: EWMADetector(),
    "ensemble": lambda s: EnsembleDetector(),
    "seasonal(auto)": lambda s: SeasonalDetector(),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    args = ap.parse_args()
    data = dataset(seeds=args.seeds)
    print(f"시계열 {len(data)}개 (패턴 6종 × 시드 {args.seeds})")
    for name, f in DETECTORS.items():
        r = evaluate_detector(name, f, data)
        print(f"\n[{name}] overall F1={r.overall_f1}")
        for k in r.by_kind:
            print(
                f"  {k.kind:15s} P={k.precision:.2f} R={k.recall:.2f} F1={k.f1:.2f} "
                f"헛알람={k.false_alarms:3d} 지연={k.mean_delay}"
            )


if __name__ == "__main__":
    main()
