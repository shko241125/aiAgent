"""인시던트 대응 v2 데모 — 서버 없이 '탐지 → RCA → 계획 → 승인 → 실행 → 효과 검증'을 한 번에.

    python scripts/demo_incident.py

시뮬레이터에 '배포 직후 커넥션 풀 고갈(롤백으로 해결)' 장애를 넣고, 사람 승인 단계에서
워크플로우가 멈췄다가 승인 후 재개되는 과정을 보여준다. 기본 Fake LLM 이라 에이전트 문장은 더미다.
"""

import asyncio
import tempfile
from pathlib import Path

from aiops.core.config import get_settings
from aiops.core.container import build_platform
from aiops.domain.models import Alert, Severity
from aiops.evals.rca import load_scenarios
from aiops.services.incident_response import respond_to_alert

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    settings = get_settings().model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/demo.db",
            "remediation_verify_interval_s": 0,
            "approval_sweep_interval_s": 0,
        }
    )
    p = await build_platform(settings)
    scenario = next(
        s for s in load_scenarios(ROOT / "data/eval/rca_scenarios.json") if s.id == "deploy-pool"
    )
    p.source.apply_scenario(scenario)

    alert = Alert(service="order-service", title="5xx 급증", severity=Severity.MAJOR)
    out = await respond_to_alert(p, alert)
    print(f"== 1. 워크플로우: {out['workflow_status']} (승인 대기에서 멈춤) ==")
    for k, v in out["steps"].items():
        print(f"  {k:9s} {v['status']}")
    apr = out["approval"]
    print(
        f"\n== 2. 승인 요청 {apr['id']} ==\n  {apr['details']['summary']}\n"
        f"  dry-run: {apr['details']['dry_run']}"
    )

    decided = await p.approvals.decide(apr["id"], True, "oncall-kim", "롤백 승인")
    await p.approvals.after_decision(decided)  # API 에서는 백그라운드로 실행됨

    inc = await p.incidents.get(out["incident_id"])
    print(f"\n== 3. 승인 후 재개 → 인시던트 상태: {inc.status} ==")
    for e in await p.incidents.timeline(out["incident_id"]):
        print(f"  {e.ts:%H:%M:%S} {e.kind:12s} [{e.actor}] {e.message[:80]}")

    print("\n== 4. 칸반 보드 ==")
    for col, cards in (await p.board(out["board_id"]).snapshot()).items():
        for c in cards:
            print(f"  {col:12s} {c['title']}")
    await p.aclose()


if __name__ == "__main__":
    asyncio.run(main())
