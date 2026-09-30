"""의존성 조립(Composition Root). 모든 구성요소를 한 곳에서 생성·연결한다.

API·CLI·테스트가 같은 Platform 객체를 공유 → 구현체 교체(Fake LLM, Qdrant 등)가 쉬워진다.
"""

from dataclasses import dataclass

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
from aiops.core.config import Settings
from aiops.db.models import create_engine, create_sessionmaker, init_db
from aiops.integrations.simulated import SimulatedOpsSource
from aiops.llm.base import LLMProvider
from aiops.llm.router import build_llm_router
from aiops.prompts.registry import PromptRegistry
from aiops.rag.hybrid import HybridRetriever
from aiops.rag.service import RAGService, build_embedder, build_vector_store


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
    source: SimulatedOpsSource
    db_engine: AsyncEngine
    sessionmaker: async_sessionmaker

    async def aclose(self) -> None:
        await self.llm.aclose()
        await self.db_engine.dispose()


async def build_platform(settings: Settings, llm: LLMProvider | None = None) -> Platform:
    llm = llm or build_llm_router(settings)
    prompts = PromptRegistry()
    source = SimulatedOpsSource()  # TODO: settings 에 따라 Prometheus/Loki 어댑터로 교체

    retriever = HybridRetriever(build_embedder(settings), build_vector_store(settings))
    rag = RAGService(retriever, llm, prompts)
    await rag.ingest_directory(settings.knowledge_dir)

    tools = ToolRegistry(ApprovalPolicy(auto_approve=settings.auto_approve_actions))
    tools.register(*build_ops_tools(source, rag))

    common = dict(max_steps=settings.agent_max_steps, memory_window=settings.memory_window)
    agents = AgentRegistry()
    agents.register(
        DetectionAgent(llm, tools, prompts, source=source, **common),
        RCAAgent(llm, tools, prompts, **common),
        RemediationAgent(llm, tools, prompts, **common),
        IncidentAgent(llm, tools, prompts, **common),
        ReportAgent(llm, tools, prompts, **common),
        KnowledgeAgent(llm, tools, prompts, **common),
    )

    engine = create_engine(settings.database_url)
    await init_db(engine)

    return Platform(
        settings=settings,
        llm=llm,
        prompts=prompts,
        tools=tools,
        agents=agents,
        orchestrator=Orchestrator(agents, llm, prompts),
        rag=rag,
        memory=InMemoryMemoryStore(),
        source=source,
        db_engine=engine,
        sessionmaker=create_sessionmaker(engine),
    )
