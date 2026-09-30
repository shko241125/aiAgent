"""LLM 라우터 (4.1, 4.6).

- 이름으로 프로바이더 선택 (에이전트별로 다른 모델 사용 가능: 예) RCA=대형, 분류=소형/구축형)
- 기본 프로바이더 실패 시 fallback 체인으로 자동 전환
- 프로바이더마다 서킷 브레이커로 장애 격리
"""

import logging
import time
from typing import Any

from aiops.core.config import Settings
from aiops.core.resilience import CircuitBreaker, retry_async
from aiops.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, ToolSpec
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.llm.usage import UsageRecord, UsageTracker, current_agent

logger = logging.getLogger(__name__)


class LLMRouter(LLMProvider):
    name = "router"

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        default: str,
        fallbacks: list[str] | None = None,
        usage: UsageTracker | None = None,
    ) -> None:
        if default not in providers:
            raise ValueError(f"default provider '{default}' is not configured")
        self.providers = providers
        self.default = default
        self.fallbacks = [f for f in (fallbacks or []) if f in providers and f != default]
        self._breakers = {name: CircuitBreaker() for name in providers}
        self.usage = usage or UsageTracker()

    def get(self, name: str | None = None) -> LLMProvider:
        return self.providers[name or self.default]

    def bind(self, provider: str | None) -> LLMProvider:
        """특정 프로바이더를 기본으로 쓰는 뷰 (에이전트별 모델, M1-03).

        fallback 체인·서킷 브레이커·사용량 집계는 원래 라우터와 공유한다.
        """
        if provider is None:
            return self
        if provider not in self.providers:
            logger.warning("provider '%s' 미설정 — 기본(%s) 사용", provider, self.default)
            return self
        return BoundLLM(self, provider)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
        provider: str | None = None,
    ) -> LLMResponse:
        chain = [provider or self.default, *self.fallbacks]
        last_exc: Exception | None = None
        for name in dict.fromkeys(chain):  # 중복 제거 + 순서 유지
            p = self.providers[name]

            async def _call(p: LLMProvider = p) -> LLMResponse:
                return await p.chat(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )

            started = time.perf_counter()
            model = getattr(p, "model", name)
            try:
                resp = await self._breakers[name].call(
                    lambda _call=_call: retry_async(_call, attempts=2)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM provider '%s' failed: %s", name, exc)
                self._record(name, model, started, ok=False)
                last_exc = exc
                continue
            self._record(name, resp.model or model, started, ok=True, usage=resp.usage)
            return resp
        raise LLMError(f"all LLM providers failed: {last_exc}")

    def _record(
        self,
        provider: str,
        model: str,
        started: float,
        *,
        ok: bool,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.usage.record(
            UsageRecord(
                provider=provider,
                model=model,
                agent=current_agent.get(),
                ok=ok,
                latency_ms=(time.perf_counter() - started) * 1000,
                **(usage or {}),
            )
        )

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()


class BoundLLM(LLMProvider):
    def __init__(self, router: LLMRouter, provider: str) -> None:
        self.router = router
        self.provider = provider
        self.name = f"router:{provider}"

    async def chat(self, messages, **kwargs) -> LLMResponse:
        kwargs.setdefault("provider", self.provider)
        return await self.router.chat(messages, **kwargs)


def build_llm_router(settings: Settings) -> LLMRouter:
    """설정에 키가 있는 프로바이더만 등록한다. fake 는 항상 등록."""
    providers: dict[str, LLMProvider] = {"fake": FakeLLMProvider()}
    t = settings.llm_timeout_s

    if settings.openai_api_key:
        from aiops.llm.providers.openai_compat import OpenAICompatProvider

        providers["openai"] = OpenAICompatProvider(
            name="openai",
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            timeout=t,
        )
    if settings.anthropic_api_key:
        from aiops.llm.providers.anthropic import AnthropicProvider

        providers["anthropic"] = AnthropicProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model, timeout=t
        )
    if settings.gemini_api_key:
        from aiops.llm.providers.gemini import GeminiProvider

        providers["gemini"] = GeminiProvider(
            api_key=settings.gemini_api_key, model=settings.gemini_model, timeout=t
        )
    wanted = {
        settings.llm_default_provider,
        *settings.fallback_providers,
        *settings.agent_provider_map.values(),
    }
    if "local" in wanted:
        from aiops.llm.providers.openai_compat import OpenAICompatProvider

        providers["local"] = OpenAICompatProvider(
            name="local",
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            timeout=t,
        )

    default = settings.llm_default_provider
    if default not in providers:
        logger.warning("LLM provider '%s' not available, falling back to 'fake'", default)
        default = "fake"
    return LLMRouter(
        providers,
        default=default,
        fallbacks=settings.fallback_providers,
        usage=UsageTracker(settings.price_table),
    )
