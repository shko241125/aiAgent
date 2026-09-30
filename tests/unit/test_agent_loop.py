from aiops.agents.base import AgentTask, LLMAgent, extract_json
from aiops.agents.context import AgentContext
from aiops.llm.base import LLMResponse, Role, ToolCall
from aiops.prompts.registry import PromptTemplate


class CalcAgent(LLMAgent):
    name = "calc"
    description = "test agent"
    prompt_name = "calc"
    tool_names = ["add"]


async def test_react_loop_calls_tool_then_answers(fake_llm, echo_tools, prompts):
    prompts.register(PromptTemplate(name="calc", system="calculator", user="$input"))
    fake_llm.push(
        LLMResponse(tool_calls=[ToolCall(id="c1", name="add", arguments={"a": 1, "b": 2})]),
        LLMResponse(content='결과는 3 입니다. {"answer": 3}'),
    )
    agent = CalcAgent(fake_llm, echo_tools, prompts)
    ctx = AgentContext()
    res = await agent.run(AgentTask(instruction="1+2?"), ctx)

    assert res.success and res.steps == 2
    assert res.data == {"answer": 3}
    assert res.tool_results[0].output == 3
    # 두 번째 LLM 호출에는 tool 결과 메시지가 포함되어야 한다
    assert fake_llm.calls[1][-1].role == Role.TOOL
    assert ctx.blackboard.read("calc.data") == {"answer": 3}


async def test_max_steps_guard(fake_llm, echo_tools, prompts):
    prompts.register(PromptTemplate(name="calc", system="s", user="$input"))
    loop = LLMResponse(tool_calls=[ToolCall(id="c", name="add", arguments={"a": 1})])
    fake_llm.push(*[loop] * 5)
    agent = CalcAgent(fake_llm, echo_tools, prompts, max_steps=3)
    res = await agent.run(AgentTask(instruction="x"), AgentContext())
    assert not res.success and res.steps == 3


def test_extract_json():
    assert extract_json('blah ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert extract_json('text {"x": {"y": 2}}') == {"x": {"y": 2}}
    assert extract_json("no json") == {}
