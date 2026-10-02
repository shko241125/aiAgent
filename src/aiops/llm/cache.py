"""LLM 응답 캐시 (M4-04 / 4.6).

같은 입력에 같은 출력이 기대될 때(temperature 0)만 캐시한다 — 그래야 캐시가 동작을 바꾸지 않는다.
키 = (프로바이더, 메시지 전체, 도구 명세, 파라미터) 의 SHA-256. 메시지에 이미 사실(fact sheet·검색
결과)이 들어 있으므로 데이터가 바뀌면 키도 바뀐다. TTL 은 모델·프롬프트 교체 후 낡은 답을 끊는 장치.

프로세스 내 LRU. TODO(4.6): 다중 인스턴스 공유가 필요하면 Redis 백엔드.
"""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from aiops.llm.base import ChatMessage, LLMResponse, ToolSpec
from aiops.observability.metrics import LLM_CACHE


def cache_key(
    provider: str,
    messages: list[ChatMessage],
    tools: list[ToolSpec] | None,
    params: dict[str, Any],
) -> str:
    payload = {
        "provider": provider,
        "messages": [m.model_dump(mode="json") for m in messages],
        "tools": [t.model_dump(mode="json") for t in tools or []],
        "params": params,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ResponseCache:
    def __init__(
        self,
        ttl_s: float = 600,
        max_entries: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self.clock = clock
        self._data: OrderedDict[str, tuple[float, LLMResponse]] = OrderedDict()
        self.hits = self.misses = 0

    def get(self, key: str) -> LLMResponse | None:
        item = self._data.get(key)
        if item is None or self.clock() - item[0] > self.ttl_s:
            self._data.pop(key, None)
            self.misses += 1
            LLM_CACHE.labels("miss").inc()
            return None
        self._data.move_to_end(key)  # LRU: 최근 사용을 뒤로
        self.hits += 1
        LLM_CACHE.labels("hit").inc()
        return item[1].model_copy(deep=True)  # 호출자가 고쳐도 캐시 원본은 그대로

    def put(self, key: str, resp: LLMResponse) -> None:
        self._data[key] = (self.clock(), resp.model_copy(deep=True))
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
