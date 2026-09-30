"""LLM 추상화 계층 (4.1).

모든 Agent 는 특정 벤더 SDK 가 아니라 이 인터페이스에만 의존한다.
→ OpenAI / Claude / Gemini / 구축형(vLLM·Ollama) 모델을 설정만으로 교체 가능.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # role=tool 일 때 어떤 호출에 대한 결과인지
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str | None, tool_calls: list[ToolCall] | None = None):
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, tool_call_id: str, name: str, content: str) -> "ChatMessage":
        return cls(role=Role.TOOL, tool_call_id=tool_call_id, name=name, content=content)


class ToolSpec(BaseModel):
    """LLM 에 노출하는 도구 명세 (JSON Schema)."""

    name: str
    description: str
    parameters: dict[str, Any]


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str = ""
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    """벤더별 구현체가 따라야 하는 계약."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...

    async def aclose(self) -> None:  # noqa: B027 - 선택적 훅
        pass
