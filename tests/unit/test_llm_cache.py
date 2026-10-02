"""M4-04 LLM 응답 캐시 — 결정적 요청만, TTL·LRU, 사용량 미집계."""

from aiops.llm.base import ChatMessage, LLMResponse
from aiops.llm.cache import ResponseCache
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.llm.router import LLMRouter

MSGS = [ChatMessage.system("s"), ChatMessage.user("order-service 5xx 원인은?")]


def router(cache, *responses):
    fake = FakeLLMProvider([LLMResponse(content=r, model="fake") for r in responses])
    return LLMRouter({"fake": fake}, default="fake", cache=cache), fake


async def test_hit_skips_provider_and_usage():
    r, fake = router(ResponseCache(), "첫 답", "두번째 답")
    a = await r.chat(MSGS)
    b = await r.chat(MSGS)
    assert a.content == b.content == "첫 답" and len(fake.calls) == 1
    assert len(r.usage.records) == 1  # 캐시 적중은 토큰·비용 0
    b.content = "변조"
    assert (await r.chat(MSGS)).content == "첫 답"  # 반환값을 고쳐도 캐시는 그대로
    assert r.cache.hit_rate == 2 / 3


async def test_nondeterministic_and_different_inputs_are_not_cached():
    r, fake = router(ResponseCache(), "a", "b", "c")
    await r.chat(MSGS, temperature=0.7)
    await r.chat(MSGS, temperature=0.7)
    assert len(fake.calls) == 2 and len(r.cache) == 0
    await r.chat(MSGS + [ChatMessage.user("추가 사실")])
    assert len(fake.calls) == 3


async def test_ttl_and_lru():
    now = [0.0]
    cache = ResponseCache(ttl_s=10, max_entries=2, clock=lambda: now[0])
    r, fake = router(cache, *"abcdef")
    m = [[ChatMessage.user(str(i))] for i in range(3)]
    await r.chat(m[0])
    await r.chat(m[1])
    await r.chat(m[0])  # 적중 → m[0] 이 최근
    await r.chat(m[2])  # 용량 2 초과 → 가장 오래 안 쓴 m[1] 제거
    assert len(fake.calls) == 3
    await r.chat(m[0])
    assert len(fake.calls) == 3
    await r.chat(m[1])
    assert len(fake.calls) == 4
    now[0] = 11  # TTL 경과
    await r.chat(m[2])
    assert len(fake.calls) == 5


async def test_cache_key_includes_provider():
    cache = ResponseCache()
    a, b = FakeLLMProvider(), FakeLLMProvider()
    r = LLMRouter({"fake": a, "local": b}, default="fake", cache=cache)
    await r.chat(MSGS)
    await r.chat(MSGS, provider="local")
    assert len(a.calls) == len(b.calls) == 1


async def test_concurrent_misses_are_coalesced():
    """캐시 스탬피드: 같은 질문이 동시에 오면 LLM 은 한 번만 호출된다 (single-flight)."""
    import asyncio

    class Slow(FakeLLMProvider):
        async def chat(self, messages, **kw):
            await asyncio.sleep(0.05)
            return await super().chat(messages, **kw)

    slow = Slow([LLMResponse(content="답", model="fake")])
    r = LLMRouter({"fake": slow}, default="fake", cache=ResponseCache())
    out = await asyncio.gather(*(r.chat(MSGS) for _ in range(10)))
    assert {o.content for o in out} == {"답"} and len(slow.calls) == 1


async def test_failed_leader_lets_waiters_retry():
    import asyncio

    class Flaky(FakeLLMProvider):
        n = 0

        async def chat(self, messages, **kw):
            await asyncio.sleep(0.02)
            Flaky.n += 1
            if Flaky.n <= 2:  # 선행 호출(재시도 2회 포함)만 실패
                raise RuntimeError("boom")
            return LLMResponse(content="ok", model="fake")

    r = LLMRouter({"fake": Flaky()}, default="fake", cache=ResponseCache())
    r.breakers["fake"].threshold = 100
    results = await asyncio.gather(r.chat(MSGS), r.chat(MSGS), return_exceptions=True)
    assert any(getattr(x, "content", None) == "ok" for x in results)
