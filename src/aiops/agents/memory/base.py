"""Agent Memory 및 Context 관리 (1.6).

메모리를 세 층으로 나눈다 (사람의 기억 구조 비유):
- ConversationMemory (단기/작업 기억)
    한 에이전트 실행 동안의 대화 메시지. 창(window) 크기로 잘라 토큰 폭증 방지
- Blackboard (공유 작업 공간)
    한 번의 협업(인시던트 대응) 동안 에이전트들이 결과를 주고받는 공유 상태
- MemoryStore (장기 기억)
    세션을 넘어 유지되는 경험 — 과거 인시던트 요약, 학습된 사실 등
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aiops.domain.models import new_id, utcnow
from aiops.llm.base import ChatMessage, Role
from aiops.rag.tokenizer import tokenize


class ConversationMemory:
    """최근 N 개 메시지 유지. system 메시지와 tool_call/tool 결과 쌍은 깨지지 않게 보존한다.

    TODO(1.6): 토큰 수 기준 트리밍, 오래된 구간 LLM 요약(summary memory)으로 압축.
    """

    def __init__(self, window: int = 20) -> None:
        self.window = window
        self._messages: list[ChatMessage] = []

    def add(self, *messages: ChatMessage) -> None:
        self._messages.extend(messages)

    def messages(self) -> list[ChatMessage]:
        system = [m for m in self._messages if m.role == Role.SYSTEM]
        rest = [m for m in self._messages if m.role != Role.SYSTEM]
        if len(rest) <= self.window:
            return system + rest
        cut = len(rest) - self.window
        # 잘린 경계가 tool 결과로 시작하면 짝이 되는 assistant(tool_calls) 가 없으므로 앞으로 당긴다
        while cut < len(rest) and rest[cut].role == Role.TOOL:
            cut += 1
        return system + rest[cut:]


class Blackboard(BaseModel):
    """에이전트 간 공유 상태. 키 규약: '<agent_name>.<field>' (예: 'rca.root_cause')."""

    data: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)

    def write(self, key: str, value: Any, author: str) -> None:
        self.data[key] = value
        self.history.append({"ts": utcnow().isoformat(), "key": key, "author": author})

    def read(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    namespace: str  # 예: "incident", "service:order-service", "agent:rca"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class MemoryStore(ABC):
    @abstractmethod
    async def add(self, item: MemoryItem) -> None: ...

    @abstractmethod
    async def search(self, namespace: str, query: str, k: int = 5) -> list[MemoryItem]: ...


class InMemoryMemoryStore(MemoryStore):
    """토큰 겹침 기반 단순 검색. TODO(1.6): Vector DB 기반 장기 기억 + 중요도·최신성 가중치."""

    def __init__(self) -> None:
        self._items: list[MemoryItem] = []

    async def add(self, item: MemoryItem) -> None:
        self._items.append(item)

    async def search(self, namespace: str, query: str, k: int = 5) -> list[MemoryItem]:
        q = set(tokenize(query))
        scored = [
            (len(q & set(tokenize(it.content))), it)
            for it in self._items
            if it.namespace == namespace
        ]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: (s[0], s[1].created_at), reverse=True)
        return [it for _, it in scored[:k]]
