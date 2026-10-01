"""운영 도구 모음 (1.5 + 2.x). 데이터 소스·RAG 를 클로저로 주입받아 Tool 을 생성한다.

새 도구 추가 절차: 1) 함수 작성 + @tool  2) 위험도(risk) 지정  3) 이 리스트에 추가
                  4) 사용할 에이전트의 tool_names 에 이름 추가
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from aiops.agents.tools.base import Tool, ToolRisk, tool
from aiops.analytics.anomaly.ensemble import default_detector
from aiops.analytics.insights import summarize_series
from aiops.analytics.rca import RCAAnalyzer
from aiops.domain.models import utcnow
from aiops.integrations.base import OpsSource

if TYPE_CHECKING:
    from aiops.rag.service import RAGService


def build_ops_tools(source: OpsSource, rag: "RAGService | None" = None) -> list[Tool]:
    @tool(tags={"observability"})
    async def query_metrics(service: str, metric: str, minutes: int = 60) -> dict:
        """서비스 메트릭 시계열을 조회하고 요약 통계와 이상 탐지 결과를 반환한다.

        metric 예: cpu_usage, memory_usage, latency_p95_ms, error_rate
        """
        end = utcnow()
        series = await source.query_range(service, metric, end - timedelta(minutes=minutes), end)
        anomalies = default_detector().detect(series.values)
        return {"service": service, "metric": metric, **summarize_series(series.values, anomalies)}

    @tool(tags={"observability"})
    async def search_logs(service: str, query: str = "error", minutes: int = 30) -> list[dict]:
        """서비스 로그에서 키워드를 검색한다 (최대 20건)."""
        end = utcnow()
        return await source.search(service, query, end - timedelta(minutes=minutes), end, limit=20)

    @tool(tags={"change"})
    async def get_recent_changes(service: str | None = None, minutes: int = 120) -> list[dict]:
        """최근 배포·설정 변경·알람 이벤트를 조회한다."""
        end = utcnow()
        events = await source.list_events(end - timedelta(minutes=minutes), end, service)
        return [e.model_dump(mode="json") for e in events]

    @tool(tags={"rca"})
    async def rank_root_causes(service: str, top_k: int = 5) -> list[dict]:
        """서비스 장애의 원인 후보를 근거와 함께 점수순으로 반환한다 (변경·로그·의존성·자원)."""
        return [c.model_dump() for c in await RCAAnalyzer(source).analyze(service, top_k)]

    @tool(tags={"topology"})
    async def get_service_dependencies(service: str) -> dict:
        """서비스의 upstream/downstream 의존성을 조회한다."""
        return await source.dependencies(service)

    @tool(risk=ToolRisk.WRITE, tags={"remediation"})
    async def restart_service(service: str, dry_run: bool = True) -> dict:
        """서비스(파드)를 롤링 재시작한다. TODO(2.3): Kubernetes API 연동."""
        return {
            "service": service,
            "action": "rolling_restart",
            "dry_run": dry_run,
            "status": "planned" if dry_run else "executed(simulated)",
        }

    @tool(risk=ToolRisk.WRITE, tags={"remediation"})
    async def scale_service(service: str, replicas: int, dry_run: bool = True) -> dict:
        """서비스 replica 수를 조정한다. TODO(2.3): HPA/Deployment 패치 연동."""
        return {
            "service": service,
            "action": "scale",
            "replicas": replicas,
            "dry_run": dry_run,
            "status": "planned" if dry_run else "executed(simulated)",
        }

    @tool(risk=ToolRisk.DESTRUCTIVE, tags={"remediation"})
    async def rollback_deployment(service: str, to_revision: str | None = None) -> dict:
        """직전(또는 지정) 리비전으로 배포를 롤백한다. 항상 사람 승인 필요."""
        return {
            "service": service,
            "action": "rollback",
            "to_revision": to_revision or "previous",
            "status": "executed(simulated)",
        }

    tools = [
        query_metrics,
        rank_root_causes,
        search_logs,
        get_recent_changes,
        get_service_dependencies,
        restart_service,
        scale_service,
        rollback_deployment,
    ]

    if rag is not None:

        @tool(tags={"knowledge"})
        async def search_knowledge(query: str, k: int = 3) -> list[dict]:
            """운영 지식베이스(runbook, 과거 장애 보고서)를 하이브리드 검색한다."""
            hits = await rag.search(query, k=k)
            return [
                {"doc_id": h.chunk.doc_id, "text": h.chunk.text[:600], "score": round(h.score, 4)}
                for h in hits
            ]

        tools.append(search_knowledge)
    return tools
