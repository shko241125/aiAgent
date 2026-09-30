import pytest

from aiops.agents.tools.base import ToolRegistry, tool
from aiops.core.config import Settings
from aiops.llm.providers.fake import FakeLLMProvider
from aiops.prompts.registry import PromptRegistry


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        llm_default_provider="fake",
        knowledge_dir="data/knowledge",
    )


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def prompts() -> PromptRegistry:
    return PromptRegistry()


@pytest.fixture
def echo_tools() -> ToolRegistry:
    @tool()
    def add(a: int, b: int = 1) -> int:
        """두 수를 더한다."""
        return a + b

    reg = ToolRegistry()
    reg.register(add)
    return reg
