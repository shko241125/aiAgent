"""벤더 HTTP 계약 테스트 (M1-03) — 실제 API 없이 요청 형식과 응답 파싱을 고정한다."""

import json

import httpx

from aiops.llm.base import ChatMessage, ToolCall, ToolSpec
from aiops.llm.providers.anthropic import AnthropicProvider
from aiops.llm.providers.gemini import GeminiProvider, _to_gemini_schema
from aiops.llm.providers.openai_compat import OpenAICompatProvider

TOOL = ToolSpec(
    name="query_metrics",
    description="d",
    parameters={
        "type": "object",
        "title": "args",
        "properties": {
            "service": {"type": "string", "title": "Service"},
            "minutes": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
        },
        "required": ["service"],
    },
)
CONVO = [
    ChatMessage.system("sys"),
    ChatMessage.user("q"),
    ChatMessage.assistant(
        None, [ToolCall(id="c1", name="query_metrics", arguments={"service": "a"})]
    ),
    ChatMessage.tool("c1", "query_metrics", '{"p95": 3}'),
]


def _client(base_url: str, reply: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=reply)

    return httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))


async def test_openai_contract():
    seen = []
    reply = {
        "model": "gpt-x",
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "t9",
                            "type": "function",
                            "function": {"name": "query_metrics", "arguments": '{"service": "b"}'},
                        }
                    ],
                },
            }
        ],
    }
    p = OpenAICompatProvider(
        name="openai",
        base_url="http://x",
        model="gpt-x",
        http_client=_client("http://x", reply, seen),
    )
    resp = await p.chat(CONVO, tools=[TOOL])
    path, body = seen[0]
    assert path == "/chat/completions"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"service": "a"}'
    assert body["messages"][3] == {"role": "tool", "content": '{"p95": 3}', "tool_call_id": "c1"}
    assert body["tools"][0]["function"]["name"] == "query_metrics"
    assert resp.tool_calls[0].arguments == {"service": "b"}
    assert resp.usage == {"input_tokens": 7, "output_tokens": 3}


async def test_anthropic_contract():
    seen = []
    reply = {
        "model": "claude-x",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 11, "output_tokens": 5},
        "content": [
            {"type": "text", "text": "확인"},
            {"type": "tool_use", "id": "tu1", "name": "query_metrics", "input": {"service": "c"}},
        ],
    }
    p = AnthropicProvider(
        api_key="k", model="claude-x", http_client=_client("https://api.anthropic.com", reply, seen)
    )
    resp = await p.chat(CONVO, tools=[TOOL])
    _, body = seen[0]
    assert body["system"] == "sys"
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "c1",
        "name": "query_metrics",
        "input": {"service": "a"},
    }
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert body["tools"][0]["input_schema"]["required"] == ["service"]
    assert resp.content == "확인" and resp.tool_calls[0].id == "tu1"
    assert resp.usage == {"input_tokens": 11, "output_tokens": 5}


async def test_gemini_contract_with_function_calling():
    seen = []
    reply = {
        "modelVersion": "gemini-x",
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4},
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {"name": "query_metrics", "args": {"service": "d"}},
                            "thoughtSignature": "sig-1",
                        }
                    ],
                },
            }
        ],
    }
    p = GeminiProvider(api_key="k", model="gemini-x", http_client=_client("https://g", reply, seen))
    resp = await p.chat(CONVO, tools=[TOOL])
    path, body = seen[0]
    assert path == "/models/gemini-x:generateContent"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "query_metrics", "args": {"service": "a"}}}],
    }
    assert body["contents"][2]["parts"][0]["functionResponse"] == {
        "name": "query_metrics",
        "response": {"p95": 3},
    }
    decl = body["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "title" not in decl and decl["properties"]["minutes"] == {
        "type": "integer",
        "nullable": True,
    }
    call = resp.tool_calls[0]
    assert call.arguments == {"service": "d"} and call.extra["thoughtSignature"] == "sig-1"
    assert resp.usage == {"input_tokens": 9, "output_tokens": 4}

    # 서명은 다음 턴에 그대로 돌려보낸다
    _, contents = GeminiProvider._convert([ChatMessage.assistant(None, [call])])
    assert contents[0]["parts"][0]["thoughtSignature"] == "sig-1"


def test_gemini_schema_sanitizer_nested():
    s = {
        "type": "object",
        "$defs": {},
        "properties": {
            "xs": {
                "type": "array",
                "title": "X",
                "items": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            }
        },
    }
    assert _to_gemini_schema(s) == {
        "type": "object",
        "properties": {"xs": {"type": "array", "items": {"type": "string", "nullable": True}}},
    }


async def test_agent_bound_to_specific_provider():
    from aiops.llm.base import LLMResponse
    from aiops.llm.providers.fake import FakeLLMProvider
    from aiops.llm.router import LLMRouter

    big = FakeLLMProvider([LLMResponse(content="big", model="big")])
    small = FakeLLMProvider([LLMResponse(content="small", model="small")])
    router = LLMRouter({"fake": small, "anthropic": big}, default="fake")
    rca_llm = router.bind("anthropic")
    assert (await rca_llm.chat([ChatMessage.user("x")])).content == "big"
    assert (await router.chat([ChatMessage.user("x")])).content == "small"
    assert router.bind("missing") is router
