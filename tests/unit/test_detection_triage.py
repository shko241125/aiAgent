"""M2-07 Detection 트리아지 — 낮은 장애 확률이면 LLM 을 호출하지 않는다."""

from pathlib import Path

import pytest

from aiops.agents.base import AgentTask
from aiops.agents.context import AgentContext
from aiops.agents.specialists.aiops import DetectionAgent
from aiops.analytics.situation_model import LogisticModel
from aiops.integrations.simulated import Fault, FaultScenario, SimulatedOpsSource
from aiops.llm.base import LLMResponse

MODEL = LogisticModel.load(Path(__file__).resolve().parents[2] / "data/models/situation_lr.json")


@pytest.fixture
def agent(fake_llm, echo_tools, prompts):
    return DetectionAgent(
        fake_llm,
        echo_tools,
        prompts,
        source=SimulatedOpsSource(seed=3),
        situation_model=MODEL,
        triage_threshold=0.05,
    )


async def test_calm_alert_is_triaged_without_llm(agent, fake_llm):
    agent.source.apply_scenario(FaultScenario(id="calm", alert_service="user-service"))
    ctx = AgentContext()
    res = await agent.run(
        AgentTask(instruction="x", inputs={"alert": {"service": "user-service", "title": "blip"}}),
        ctx,
    )
    assert res.data["is_incident"] is False and "트리아지" in res.output
    assert fake_llm.calls == []  # LLM 호출 0회
    assert any(t.kind == "pre_decided" for t in ctx.trace)


async def test_real_incident_goes_to_llm_with_learned_context(agent, fake_llm):
    agent.source.apply_scenario(
        FaultScenario(
            id="f",
            alert_service="order-service",
            faults=[
                Fault(service="order-service", metric="error_rate", magnitude=12, onset_min_ago=15)
            ],
        )
    )
    fake_llm.push(LLMResponse(content='{"is_incident": true, "severity": "major"}'))
    ctx = AgentContext()
    res = await agent.run(
        AgentTask(instruction="x", inputs={"alert": {"service": "order-service", "title": "5xx"}}),
        ctx,
    )
    assert res.data["is_incident"] is True and len(fake_llm.calls) == 1
    sit = ctx.blackboard.read("detection.situation")
    assert sit["learned"]["risk_score"] > 50 and "error_score" in str(sit["learned"]["factors"])
    assert "learned" in fake_llm.calls[0][1].content  # 학습 판정이 프롬프트에 들어간다


async def test_downstream_failure_propagates_risk(agent):
    agent.source.apply_scenario(
        FaultScenario(
            id="d",
            alert_service="order-service",
            faults=[
                Fault(service="payment-db", metric="latency_p95_ms", magnitude=8, onset_min_ago=20),
                Fault(
                    service="payment-service", metric="error_rate", magnitude=6, onset_min_ago=18
                ),
            ],
        )
    )
    ctx = AgentContext()
    variables = await agent.build_input(
        AgentTask(instruction="x", inputs={"alert": {"service": "order-service"}}), ctx
    )
    sit = ctx.blackboard.read("detection.situation")
    assert sit["propagated_risk"] >= 0.3  # 자기 지표는 멀쩡해도 하위 장애로 위험 상승
    assert "payment-db" in variables["situation"]
