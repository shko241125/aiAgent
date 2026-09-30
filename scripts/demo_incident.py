"""인시던트 대응 파이프라인을 API 서버 없이 한 번에 실행해 보는 데모.

    python scripts/demo_incident.py

기본은 Fake LLM 이라 에이전트 응답은 더미지만, 이상 탐지 → 상황 인식 → 워크플로우 실행 →
도구 호출 추적(trace) 흐름을 그대로 확인할 수 있다.
.env 에 실제 LLM 키를 넣으면 실제 추론이 수행된다.
"""

import asyncio
import json
import tempfile

from aiops.agents.context import AgentContext
from aiops.agents.orchestration.workflows import (
    build_incident_response,
    finalize_incident_board,
)
from aiops.core.config import get_settings
from aiops.core.container import build_platform
from aiops.domain.models import Alert, Severity


async def main() -> None:
    settings = get_settings().model_copy(
        update={"database_url": f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/demo.db"}
    )
    platform = await build_platform(settings)
    platform.source.inject_incident("order-service", "latency_p95_ms", magnitude=4.0)
    platform.source.inject_incident("order-service", "error_rate", magnitude=10.0)

    alert = Alert(service="order-service", title="p95 latency > 2s", severity=Severity.MAJOR)
    ctx = AgentContext(incident_id="demo", long_term=platform.memory, board=platform.board("demo"))
    wf, cards = await build_incident_response(platform.orchestrator, ctx, title=alert.title)
    result = await platform.orchestrator.run_workflow(
        wf, ctx, state={"alert": alert.model_dump(mode="json")}
    )
    await finalize_incident_board(ctx, result.workflow_run, cards)

    print("== 상황 인식 (3.2) ==")
    print(json.dumps(ctx.blackboard.read("detection.situation"), ensure_ascii=False, indent=2))
    print("\n== 워크플로우 단계 (1.3/4.5) ==")
    for step_id, rec in result.workflow_run.steps.items():
        print(f"  {step_id:10s} {rec.status}")
    print("\n== 칸반 보드 (PLAN-0001) ==")
    for col, items in (await ctx.board.snapshot()).items():
        for c in items:
            print(f"  {col:12s} {c['id']:12s} {c['title']}")
    print("\n== 메모리 없는 에이전트가 읽는 브리핑 (RCA 카드) ==")
    print(await ctx.board.briefing(cards["rca"]))
    print("\n== 실행 추적 trace (1.6/4.6) ==")
    for t in ctx.trace:
        print(f"  {t.agent:12s} {t.kind:10s} {json.dumps(t.data, ensure_ascii=False)[:90]}")
    await platform.aclose()


if __name__ == "__main__":
    asyncio.run(main())
