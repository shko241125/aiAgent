"""의존성 조립(Composition Root). 모든 구성요소를 한 곳에서 생성·연결한다.

API·CLI·테스트가 같은 Platform 객체를 공유 → 구현체 교체(Fake LLM, Qdrant 등)가 쉬워진다.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from aiops.agents.memory.base import InMemoryMemoryStore, MemoryStore
from aiops.agents.orchestration.orchestrator import Orchestrator
from aiops.agents.registry import AgentRegistry
from aiops.agents.specialists.aiops import (
    DetectionAgent,
    IncidentAgent,
    KnowledgeAgent,
    RCAAgent,
    RemediationAgent,
    ReportAgent,
)
from aiops.agents.tools.base import ApprovalPolicy, ToolRegistry
from aiops.agents.tools.builtin.ops import build_ops_tools
from aiops.analytics.situation_model import LogisticModel
from aiops.core.config import Settings
from aiops.db.models import create_engine, create_sessionmaker, init_db
from aiops.integrations.base import OpsSource
from aiops.integrations.events import SqlEventStore
from aiops.integrations.factory import build_ops_source
from aiops.kanban.board import KanbanBoard
from aiops.kanban.stores import BoardStore, SqlBoardStore
from aiops.kanban.tools import build_kanban_tools
from aiops.llm.base import LLMProvider
from aiops.llm.router import LLMRouter, build_llm_router
from aiops.observability.metrics import observe_step
from aiops.prompts.registry import PromptRegistry
from aiops.rag.hybrid import HybridRetriever
from aiops.rag.knowledge import KnowledgeRepository
from aiops.rag.service import RAGService, build_embedder, build_reranker, build_vector_store
from aiops.remediation.executors import build_executor
from aiops.remediation.guardrails import Guardrails
from aiops.remediation.service import RemediationService
from aiops.services import incident_response
from aiops.services.approvals import ApprovalService
from aiops.services.incidents import IncidentService
from aiops.services.notify import LogNotifier, Notifier, SlackWebhookNotifier
from aiops.workflow.engine import WorkflowEngine
from aiops.workflow.store import SqlRunStore


@dataclass
class Platform:
    settings: Settings
    llm: LLMProvider
    prompts: PromptRegistry
    tools: ToolRegistry
    agents: AgentRegistry
    orchestrator: Orchestrator
    rag: RAGService
    memory: MemoryStore
    source: OpsSource
    db_engine: AsyncEngine
    sessionmaker: async_sessionmaker
    board_store: BoardStore
    knowledge: KnowledgeRepository
    events: SqlEventStore
    incidents: IncidentService
    approvals: ApprovalService
    notifier: Notifier
    remediation: RemediationService

    async def resume_workflow(self, run_id: str) -> None:
        """승인 결정 뒤 워크플로우 재개 (팩토리가 등록된 워크플로우만)."""
        try:
            await self.orchestrator.engine.resume(run_id)
        except KeyError as exc:
            logging.getLogger(__name__).warning("재개 불가 %s: %s", run_id, exc)

    def board(self, board_id: str) -> KanbanBoard:
        """보드 = 작업 공간 단위 (인시던트 1건, 목표 1개 등). 저장소는 DB 로 영속."""
        return KanbanBoard(self.board_store, board_id)

    async def aclose(self) -> None:
        await self.llm.aclose()
        await self.source.aclose()
        await self.db_engine.dispose()


async def build_platform(settings: Settings, llm: LLMProvider | None = None) -> Platform:
    llm = llm or build_llm_router(settings)
    prompts = PromptRegistry()
    engine = create_engine(settings.database_url)
    await init_db(engine, settings.schema_mode)
    sessionmaker = create_sessionmaker(engine)
    events = SqlEventStore(sessionmaker)  # 웹훅 수집 이벤트 (M2-02)
    # simulated | live(Prometheus + Loki + 웹훅 이벤트 DB + 토폴로지 파일)
    source = build_ops_source(settings, events=events)

    retriever = HybridRetriever(
        build_embedder(settings),
        build_vector_store(settings),
        reranker=build_reranker(settings, llm),
    )
    rag = RAGService(retriever, llm, prompts)
    await rag.ingest_directory(settings.knowledge_dir)

    tools = ToolRegistry(ApprovalPolicy(auto_approve=settings.auto_approve_actions))
    tools.register(*build_ops_tools(source, rag), *build_kanban_tools())

    common = dict(max_steps=settings.agent_max_steps, memory_window=settings.memory_window)
    agent_map = settings.agent_provider_map

    def llm_for(agent: str) -> LLMProvider:  # 에이전트별 모델 (M1-03)
        return llm.bind(agent_map.get(agent)) if isinstance(llm, LLMRouter) else llm

    agents = AgentRegistry()
    agents.register(
        DetectionAgent(
            llm_for("detection"),
            tools,
            prompts,
            source=source,
            situation_model=LogisticModel.load(settings.situation_model_path),
            triage_threshold=settings.detection_triage_threshold,
            **common,
        ),
        RCAAgent(llm_for("rca"), tools, prompts, source=source, **common),
        RemediationAgent(llm_for("remediation"), tools, prompts, **common),
        IncidentAgent(llm_for("incident"), tools, prompts, **common),
        ReportAgent(llm_for("report"), tools, prompts, **common),
        KnowledgeAgent(llm_for("knowledge"), tools, prompts, **common),
    )

    knowledge = KnowledgeRepository(sessionmaker)
    await rag.ingest(await knowledge.all())  # 운영 중 축적된 지식 재적재 (M1-09)

    incident_service = IncidentService(sessionmaker)
    notifier: Notifier = (
        SlackWebhookNotifier(settings.slack_webhook_url, settings.slack_escalation_mention)
        if settings.slack_webhook_url
        else LogNotifier()
    )
    approvals = ApprovalService(
        sessionmaker,
        notifier,
        escalate_after=timedelta(minutes=settings.approval_escalate_after_min),
        timeout=timedelta(minutes=settings.approval_timeout_min),
        record=incident_service.record,
    )
    # 체크포인트·승인 대기·재개가 가능한 엔진 (M3-01/05)
    wf_engine = WorkflowEngine(
        store=SqlRunStore(sessionmaker), approvals=approvals, on_step_update=observe_step
    )

    platform = Platform(
        settings=settings,
        llm=llm,
        prompts=prompts,
        tools=tools,
        agents=agents,
        orchestrator=Orchestrator(agents, llm, prompts, engine=wf_engine),
        rag=rag,
        memory=InMemoryMemoryStore(),
        source=source,
        db_engine=engine,
        sessionmaker=sessionmaker,
        board_store=SqlBoardStore(sessionmaker),
        knowledge=knowledge,
        events=events,
        incidents=incident_service,
        approvals=approvals,
        notifier=notifier,
        remediation=RemediationService(
            build_executor(settings, source), Guardrails(settings.guardrail_policy)
        ),
    )
    approvals.on_decided = lambda a: platform.resume_workflow(a.run_id)
    incident_response.register(platform)  # 재시작 후 재개용 팩토리·완료 훅 (M3-06)
    return platform
