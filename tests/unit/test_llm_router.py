import pytest

from aiops.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse
from aiops.llm.providers.anthropic import AnthropicProvider
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.llm.router import LLMRouter


class Broken(LLMProvider):
    name = "broken"

    async def chat(self, messages, **kwargs):
        raise LLMError("down")


async def test_router_falls_back():
    router = LLMRouter(
        {"broken": Broken(), "fake": FakeLLMProvider()}, default="broken", fallbacks=["fake"]
    )
    resp = await router.chat([ChatMessage.user("hi")])
    assert resp.model == "fake"


async def test_router_raises_when_all_fail():
    router = LLMRouter({"broken": Broken()}, default="broken")
    with pytest.raises(LLMError):
        await router.chat([ChatMessage.user("hi")])


def test_anthropic_message_conversion():
    from aiops.llm.base import ToolCall

    msgs = [
        ChatMessage.system("sys"),
        ChatMessage.user("q"),
        ChatMessage.assistant(
            None,
            [
                ToolCall(id="t1", name="f", arguments={"x": 1}),
                ToolCall(id="t2", name="g", arguments={}),
            ],
        ),
        ChatMessage.tool("t1", "f", "r1"),
        ChatMessage.tool("t2", "g", "r2"),
    ]
    system, wire = AnthropicProvider._convert(msgs)
    assert system == "sys"
    assert wire[1]["content"][0]["type"] == "tool_use"
    # 연속된 tool 결과는 하나의 user 메시지로 병합
    assert wire[2]["role"] == "user" and len(wire[2]["content"]) == 2


def test_fake_default_response():
    import asyncio

    resp = asyncio.run(FakeLLMProvider().chat([ChatMessage.user("hello\nworld")]))
    assert resp == LLMResponse(content="[fake-llm] hello", model="fake", finish_reason="stop")
