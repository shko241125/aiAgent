"""OpenAI Chat Completions 호환 프로바이더.

OpenAI 본가뿐 아니라 vLLM, Ollama, LM Studio, TGI 등 구축형 LLM 서버가 모두
같은 `/chat/completions` 스펙을 제공하므로 하나의 구현으로 재사용한다.
"""

import json
from typing import Any

import httpx

from aiops.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, Role, ToolCall, ToolSpec


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)

    @staticmethod
    def _to_wire(msg: ChatMessage) -> dict[str, Any]:
        out: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
        if msg.role == Role.ASSISTANT and msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in msg.tool_calls
            ]
        if msg.role == Role.TOOL:
            out["tool_call_id"] = msg.tool_call_id
        return out

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [self._to_wire(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        if response_format:
            body["response_format"] = response_format

        resp = await self._client.post("/chat/completions", json=body)
        if resp.status_code >= 400:
            raise LLMError(f"[{self.name}] {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]
        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"].get("arguments") or "{}"),
            )
            for tc in message.get("tool_calls") or []
        ]
        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage") or {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
