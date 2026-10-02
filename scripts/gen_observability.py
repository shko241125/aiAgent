"""SLO 정의(src/aiops/observability/slo.py) → Prometheus 규칙·Grafana 대시보드 생성 (M4-03).

python scripts/gen_observability.py          # deploy/ 아래 생성물 갱신
python scripts/gen_observability.py --check  # 생성물이 최신인지 검사 (불일치 시 exit 1)
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from aiops.observability.slo import grafana_dashboard, prometheus_rules

ROOT = Path(__file__).resolve().parents[1]
HEADER = "# 자동 생성 — 직접 고치지 말 것. 원본: src/aiops/observability/slo.py\n"


def render() -> dict[Path, str]:
    rules = yaml.safe_dump(prometheus_rules(), allow_unicode=True, sort_keys=False, width=1000)
    dash = json.dumps(grafana_dashboard(), ensure_ascii=False, indent=2) + "\n"
    return {
        ROOT / "deploy/prometheus/aiops-slo-rules.yml": HEADER + rules,
        ROOT / "deploy/grafana/aiops-slo-dashboard.json": dash,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    stale = []
    for path, content in render().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"생성: {path.relative_to(ROOT)}")
    if stale:
        print(f"생성물이 SLO 정의와 다릅니다: {stale} → python scripts/gen_observability.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
