"""Google Gemini (generateContent REST) 프로바이더.

TODO(4.1): function calling(tools) 변환, 스트리밍, safety 설정.
"""

from typing import Any

import httpx

from aiops.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, Role, ToolSpec

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, timeout: float = 60.0) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=API_BASE, headers={"x-goog-api-key": api_key}, timeout=timeout
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if tools:
            raise NotImplementedError("Gemini tool calling 은 아직 미구현 (ROADMAP 4.1)")
        system = "\n\n".join(m.content or "" for m in messages if m.role == Role.SYSTEM)
        contents = [
            {
                "role": "model" if m.role == Role.ASSISTANT else "user",
                "parts": [{"text": m.content or ""}],
            }
            for m in messages
            if m.role in (Role.USER, Role.ASSISTANT)
        ]
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        resp = await self._client.post(f"/models/{self.model}:generateContent", json=body)
        if resp.status_code >= 400:
            raise LLMError(f"[gemini] {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        parts = data["candidates"][0]["content"].get("parts", [])
        return LLMResponse(
            content="".join(p.get("text", "") for p in parts) or None,
            model=self.model,
            finish_reason=data["candidates"][0].get("finishReason"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
