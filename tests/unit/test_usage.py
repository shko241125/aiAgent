from aiops.agents.base import AgentTask, LLMAgent
from aiops.agents.context import AgentContext
from aiops.llm.base import ChatMessage, LLMResponse
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.llm.router import LLMRouter
from aiops.llm.usage import UsageTracker, normalize_usage
from aiops.prompts.registry import PromptTemplate


def test_normalize_usage_across_providers():
    assert normalize_usage({"prompt_tokens": 3, "completion_tokens": 4}) == {
        "input_tokens": 3,
        "output_tokens": 4,
    }
    assert normalize_usage({"input_tokens": 5, "output_tokens": 6})["output_tokens"] == 6
    assert normalize_usage({"promptTokenCount": 1, "candidatesTokenCount": 2})["input_tokens"] == 1
    assert normalize_usage(None) == {"input_tokens": 0, "output_tokens": 0}


async def test_router_attributes_usage_to_agent(echo_tools, prompts):
    fake = FakeLLMProvider(
        [LLMResponse(content="a", model="m1", usage={"input_tokens": 10, "output_tokens": 2})]
    )
    router = LLMRouter({"fake": fake}, default="fake", usage=UsageTracker({"m1": (1.0, 2.0)}))

    class A(LLMAgent):
        name, description, prompt_name = "rca", "d", "p"

    prompts.register(PromptTemplate(name="p", system="s", user="$input"))
    await A(router, echo_tools, prompts).run(AgentTask(instruction="x"), AgentContext())
    await router.chat([ChatMessage.user("no agent")])

    by_agent = {g.key: g for g in router.usage.summary("agent")}
    assert by_agent["rca"].input_tokens == 10 and by_agent["rca"].calls == 1
    assert "-" in by_agent  # 에이전트 밖 호출
    by_model = {g.key: g for g in router.usage.summary("model")}
    assert by_model["m1"].cost_usd == round(10 / 1e6 * 1.0 + 2 / 1e6 * 2.0, 6)
    assert by_model["fake"].cost_usd is None  # 단가 미설정 → 추정하지 않음
