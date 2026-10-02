"""M4-05 토큰 예산 컨텍스트 — 50+ step 루프에서 예산 초과 0회, 과제 고정, 도구 쌍 보존, 요약."""

from aiops.agents.base import AgentTask, LLMAgent
from aiops.agents.context import AgentContext
from aiops.agents.memory.base import (
    ConversationMemory,
    estimate_tokens,
    total_tokens,
)
from aiops.agents.tools.base import ToolRegistry, tool
from aiops.llm.base import ChatMessage, LLMProvider, LLMResponse, Role, ToolCall
from aiops.prompts.registry import PromptRegistry, PromptTemplate

BUDGET = 3000
TASK = "order-service 장애 원인을 로그 60개 구간을 차례로 조사해 찾아라"


def test_estimate_tokens_is_conservative_for_korean():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("커넥션 풀 고갈") >= 7  # 한글 음절 = 최소 1 토큰


def test_task_message_survives_window_overflow():
    """회귀: 예전엔 창(20개)을 넘으면 첫 user 메시지(과제)까지 잘려 나갔다."""
    mem = ConversationMemory(window=4)
    mem.add(ChatMessage.system("s"), ChatMessage.user(TASK))
    for i in range(10):
        mem.add(ChatMessage.assistant(f"생각 {i}"), ChatMessage.user(f"관찰 {i}"))
    msgs = mem.messages()
    assert msgs[1].content == TASK and len(msgs) == 2 + 4


def test_giant_tool_result_is_truncated_to_fit():
    mem = ConversationMemory(max_tokens=500)
    mem.add(ChatMessage.system("s"), ChatMessage.user("t"))
    mem.add(ChatMessage.assistant(None, [ToolCall(id="1", name="logs")]))
    mem.add(ChatMessage.tool("1", "logs", "ERROR 커넥션 " * 2000))
    msgs = mem.messages()
    assert total_tokens(msgs) <= 500 and msgs[-1].content.endswith("…(생략)")


class Scripted(LLMProvider):
    """60번 도구를 부른 뒤 결론. 요약 요청은 별도로 응답하고, 매 호출의 컨텍스트를 검사한다."""

    name = "scripted"

    def __init__(self, steps: int, fail_summary: bool = False) -> None:
        self.steps, self.fail_summary = steps, fail_summary
        self.agent_calls: list[list[ChatMessage]] = []
        self.summary_calls = 0

    async def chat(self, messages, **kw) -> LLMResponse:
        if len(messages) == 1 and "오래된 구간을 압축" in (messages[0].content or ""):
            self.summary_calls += 1
            if self.fail_summary:
                raise RuntimeError("요약 모델 장애")
            return LLMResponse(
                content=f"- 구간 {self.summary_calls}: 로그에서 HikariPool 경고 확인"
            )
        self.agent_calls.append(list(messages))
        n = len(self.agent_calls)
        if n <= self.steps:
            call = ToolCall(id=f"c{n}", name="read_logs", arguments={"segment": n})
            return LLMResponse(content=f"{n}번째 구간을 읽겠습니다", tool_calls=[call])
        return LLMResponse(content='원인: 커넥션 풀 고갈 {"root_cause": "pool"}')


@tool()
def read_logs(segment: int) -> str:
    """로그 구간 조회."""
    return f"[segment {segment}] " + "ERROR HikariPool-1 - Connection is not available. " * 12


class LongAgent(LLMAgent):
    name = "long"
    description = "test"
    prompt_name = "long"
    tool_names = ["read_logs"]


def _check_calls(llm: Scripted) -> None:
    for msgs in llm.agent_calls:
        assert total_tokens(msgs) <= BUDGET, "컨텍스트 예산 초과"
        assert msgs[1].role == Role.USER and msgs[1].content.startswith(TASK), "과제 유실"
        issued = set()
        for m in msgs:  # 모든 tool 결과는 같은 요청 안에 짝(assistant tool_call)이 있어야 한다
            issued |= {c.id for c in m.tool_calls}
            if m.role == Role.TOOL:
                assert m.tool_call_id in issued, "짝 잃은 tool 결과"


async def _run(llm: Scripted) -> tuple:
    prompts = PromptRegistry()
    prompts.register(PromptTemplate(name="long", system="조사 에이전트", user="$input"))
    tools = ToolRegistry()
    tools.register(read_logs)
    agent = LongAgent(llm, tools, prompts, max_steps=70, memory_window=20, context_tokens=BUDGET)
    ctx = AgentContext()
    return await agent.run(AgentTask(instruction=TASK), ctx), ctx


async def test_60_step_loop_never_exceeds_budget():
    llm = Scripted(steps=60)
    res, ctx = await _run(llm)
    assert res.success and res.steps == 61 and res.data == {"root_cause": "pool"}
    _check_calls(llm)
    compactions = [e for e in ctx.trace if e.kind == "context_compacted"]
    assert 1 <= len(compactions) <= 61 // 4  # 저수위까지 줄여 요약 호출이 드물다
    assert llm.summary_calls == len(compactions)
    assert "HikariPool" in llm.agent_calls[-1][1].content  # 요약이 과제 뒤에 실려 간다


async def test_summary_failure_falls_back_to_truncation():
    llm = Scripted(steps=55, fail_summary=True)
    res, _ = await _run(llm)
    assert res.success
    _check_calls(llm)
    assert "요약 실패" in llm.agent_calls[-1][1].content
