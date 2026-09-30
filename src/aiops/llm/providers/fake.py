"""테스트·로컬 개발용 결정적(deterministic) LLM.

API 키 없이 전체 파이프라인을 돌려볼 수 있게 해 준다.
- scripted 응답 리스트를 주면 순서대로 반환 (도구 호출 시나리오 테스트)
- 없으면 마지막 user 메시지를 요약한 고정 문자열을 반환
"""

from collections import deque
from collections.abc import Iterable
from typing import Any

from aiops.llm.base import ChatMessage, LLMProvider, LLMResponse, Role, ToolSpec


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(self, scripted: Iterable[LLMResponse] | None = None) -> None:
        self._scripted: deque[LLMResponse] = deque(scripted or [])
        self.calls: list[list[ChatMessage]] = []

    def push(self, *responses: LLMResponse) -> None:
        self._scripted.extend(responses)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if self._scripted:
            return self._scripted.popleft()
        last_user = next((m.content for m in reversed(messages) if m.role == Role.USER), "")
        preview = (last_user or "").strip().splitlines()[0][:120] if last_user else ""
        return LLMResponse(content=f"[fake-llm] {preview}", model="fake", finish_reason="stop")
