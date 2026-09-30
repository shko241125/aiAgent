from pydantic import BaseModel, Field

from aiops.agents.base import AgentTask, LLMAgent
from aiops.agents.context import AgentContext
from aiops.llm.base import LLMResponse, Role
from aiops.prompts.registry import PromptTemplate


class Out(BaseModel):
    root_cause: str
    confidence: float = Field(ge=0, le=1)


class Typed(LLMAgent):
    name, description, prompt_name = "typed", "t", "t"
    output_model = Out


async def test_invalid_output_is_repaired_once(fake_llm, echo_tools, prompts):
    prompts.register(PromptTemplate(name="t", system="s", user="$input"))
    fake_llm.push(
        LLMResponse(content='분석... {"root_cause": "pool", "confidence": 7}'),
        LLMResponse(content='{"root_cause": "pool", "confidence": 0.7}'),
    )
    ctx = AgentContext()
    res = await Typed(fake_llm, echo_tools, prompts).run(AgentTask(instruction="x"), ctx)
    assert res.data == {"root_cause": "pool", "confidence": 0.7}
    assert not res.schema_errors and res.steps == 2
    repair = fake_llm.calls[1][-1]
    assert repair.role == Role.USER and "confidence" in repair.content
    assert any(t.kind == "schema_retry" for t in ctx.trace)


async def test_errors_kept_after_retries_exhausted(fake_llm, echo_tools, prompts):
    prompts.register(PromptTemplate(name="t", system="s", user="$input"))
    fake_llm.push(LLMResponse(content="no json"), LLMResponse(content="still none"))
    res = await Typed(fake_llm, echo_tools, prompts).run(AgentTask(instruction="x"), AgentContext())
    assert res.success and res.schema_errors == ["최종 답변에 JSON 객체가 없습니다"]
    assert res.steps == 2
