"""Anthropic Claude Messages API 프로바이더 (httpx 직접 호출).

OpenAI 포맷과의 차이:
- system 프롬프트는 messages 가 아니라 최상위 `system` 필드
- 도구 호출은 assistant content 의 `tool_use` 블록, 결과는 user content 의 `tool_result` 블록
"""

from typing import Any

import httpx

from aiops.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, Role, ToolCall, ToolSpec

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, timeout: float = 60.0) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
        )

    @staticmethod
    def _convert(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system_parts.append(m.content or "")
            elif m.role == Role.USER:
                wire.append({"role": "user", "content": m.content or ""})
            elif m.role == Role.ASSISTANT:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    )
                wire.append({"role": "assistant", "content": blocks})
            elif m.role == Role.TOOL:
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content or "",
                }
                # 연속된 tool 결과는 하나의 user 메시지로 묶어야 한다.
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
        return ("\n\n".join(system_parts) or None), wire

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        system, wire = self._convert(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": wire,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        resp = await self._client.post(API_URL, json=body)
        if resp.status_code >= 400:
            raise LLMError(f"[anthropic] {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        texts, calls = [], []
        for block in data.get("content", []):
            if block["type"] == "text":
                texts.append(block["text"])
            elif block["type"] == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block["input"]))
        return LLMResponse(
            content="\n".join(texts) or None,
            tool_calls=calls,
            model=data.get("model", self.model),
            finish_reason=data.get("stop_reason"),
            usage=data.get("usage") or {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
