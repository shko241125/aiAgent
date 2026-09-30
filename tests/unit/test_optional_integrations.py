"""선택 의존성(.[agents], .[ml]) 통합 테스트 — 미설치 환경에서는 skip."""

import numpy as np
import pytest

from aiops.agents.base import LLMAgent
from aiops.agents.context import AgentContext
from aiops.agents.registry import AgentRegistry
from aiops.prompts.registry import PromptTemplate


async def test_langgraph_linear_graph(fake_llm, echo_tools, prompts):
    pytest.importorskip("langgraph")
    from aiops.agents.orchestration.langgraph_adapter import build_linear_graph

    prompts.register(PromptTemplate(name="p", system="s", user="$input"))

    class A(LLMAgent):
        name, description, prompt_name = "a", "a", "p"

    class B(LLMAgent):
        name, description, prompt_name = "b", "b", "p"

    reg = AgentRegistry()
    reg.register(A(fake_llm, echo_tools, prompts), B(fake_llm, echo_tools, prompts))
    graph = build_linear_graph(reg, ["a", "b"], AgentContext())
    out = await graph.ainvoke({"instruction": "hello", "inputs": {}, "outputs": {}})
    assert set(out["outputs"]) == {"a", "b"}


def test_isolation_forest_detects_spike():
    pytest.importorskip("sklearn")
    from aiops.analytics.anomaly.ml import IsolationForestDetector

    x = (50 + np.random.default_rng(0).normal(0, 1, 200)).tolist()
    x[150] = 95
    idx = [a.index for a in IsolationForestDetector(contamination=0.01).detect(x)]
    assert 150 in idx
