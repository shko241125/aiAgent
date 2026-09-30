"""Google Gemini (generateContent REST) 프로바이더 — function calling 포함 (M1-03).

OpenAI/Claude 와의 차이:
- system 은 `systemInstruction`, assistant 역할명은 "model"
- 도구 호출은 `functionCall` part, 결과는 user 턴의 `functionResponse` part
  (호출 id 가 없어 name 으로 매칭)
- 도구 파라미터 스키마는 OpenAPI 부분집합 → pydantic JSON Schema 를 정리해서 보낸다
- 사고(thinking) 모델은 functionCall part 에 thoughtSignature 를 붙여 준다
  → 다음 턴에 그대로 돌려보낸다

TODO(4.1): 스트리밍, safety 설정.
"""

import json
from typing import Any

import httpx

from aiops.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, Role, ToolCall, ToolSpec
from aiops.llm.usage import normalize_usage

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_DROP_KEYS = {"title", "default", "additionalProperties", "$defs", "$schema", "examples"}


def _to_gemini_schema(schema: Any) -> Any:
    """JSON Schema → Gemini(OpenAPI 3 부분집합). `anyOf: [X, null]` 은 X + nullable 로 접는다."""
    if isinstance(schema, list):
        return [_to_gemini_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        nullable = len(options) < len(schema["anyOf"])
        if len(options) == 1:
            merged = {**{k: v for k, v in schema.items() if k != "anyOf"}, **options[0]}
            out = _to_gemini_schema(merged)
            if nullable:
                out["nullable"] = True
            return out
    return {k: _to_gemini_schema(v) for k, v in schema.items() if k not in _DROP_KEYS}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._client = http_client or httpx.AsyncClient(
            base_url=API_BASE, headers={"x-goog-api-key": api_key}, timeout=timeout
        )

    @staticmethod
    def _convert(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system = "\n\n".join(m.content or "" for m in messages if m.role == Role.SYSTEM)
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.USER:
                contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
            elif m.role == Role.ASSISTANT:
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    part: dict[str, Any] = {"functionCall": {"name": tc.name, "args": tc.arguments}}
                    if sig := tc.extra.get("thoughtSignature"):
                        part["thoughtSignature"] = sig
                    parts.append(part)
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            elif m.role == Role.TOOL:
                try:
                    payload = json.loads(m.content or "null")
                except json.JSONDecodeError:
                    payload = m.content
                if not isinstance(payload, dict):
                    payload = {"result": payload}
                part = {"functionResponse": {"name": m.name, "response": payload}}
                last = contents[-1] if contents else None
                if last and last["role"] == "user" and "functionResponse" in last["parts"][0]:
                    last["parts"].append(part)  # 병렬 호출 결과는 한 턴에 모은다
                else:
                    contents.append({"role": "user", "parts": [part]})
        return system, contents

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        system, contents = self._convert(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": _to_gemini_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]
        resp = await self._client.post(f"/models/{self.model}:generateContent", json=body)
        if resp.status_code >= 400:
            raise LLMError(f"[gemini] {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        cand = data["candidates"][0]
        texts, calls = [], []
        for i, part in enumerate(cand.get("content", {}).get("parts", [])):
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            elif fc := part.get("functionCall"):
                extra = (
                    {"thoughtSignature": part["thoughtSignature"]}
                    if "thoughtSignature" in part
                    else {}
                )
                calls.append(
                    ToolCall(
                        id=f"call_{i}_{fc['name']}",
                        name=fc["name"],
                        arguments=fc.get("args") or {},
                        extra=extra,
                    )
                )
        return LLMResponse(
            content="".join(texts) or None,
            tool_calls=calls,
            model=data.get("modelVersion", self.model),
            finish_reason=cand.get("finishReason"),
            usage=normalize_usage(data.get("usageMetadata")),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
