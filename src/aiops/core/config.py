"""애플리케이션 설정. 모든 값은 `AIOPS_` 접두사 환경변수 또는 .env 로 주입한다."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AIOPS_", extra="ignore")

    app_name: str = "AIOps Autonomous Operations Platform"
    env: Literal["local", "dev", "prod"] = "local"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./aiops.db"

    # --- LLM (4.1) ---
    llm_default_provider: str = "fake"
    llm_fallback_providers: str = ""  # 쉼표 구분, 예: "anthropic,local"
    llm_timeout_s: float = 60.0
    # 에이전트별 프로바이더: "rca=anthropic,detection=local" (미지정 에이전트는 기본값)
    agent_llm_providers: str = ""
    # 비용 추정용 단가(JSON): {"<model>": [USD/1M 입력토큰, USD/1M 출력토큰]} — 계약 단가로 설정
    llm_prices: str = "{}"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen3"

    # --- RAG (4.2) ---
    embedding_provider: Literal["hash", "openai", "local"] = "hash"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 384
    vector_backend: Literal["memory", "qdrant"] = "memory"
    qdrant_url: str = "http://localhost:6333"
    knowledge_dir: Path = Path("data/knowledge")
    # none | http(TEI·Jina 호환 cross-encoder) | llm
    reranker: Literal["none", "http", "llm"] = "none"
    reranker_url: str = "http://localhost:8080"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_api_style: Literal["tei", "jina"] = "tei"

    # --- Agent (1.x) ---
    agent_max_steps: int = 8
    memory_window: int = 20

    # --- 운영 자동화 안전장치 (2.3) ---
    auto_approve_actions: bool = False

    @property
    def price_table(self) -> dict[str, tuple[float, float]]:
        import json

        return {k: (float(v[0]), float(v[1])) for k, v in json.loads(self.llm_prices).items()}

    @property
    def agent_provider_map(self) -> dict[str, str]:
        pairs = (p.split("=", 1) for p in self.agent_llm_providers.split(",") if "=" in p)
        return {a.strip(): v.strip() for a, v in pairs}

    @property
    def fallback_providers(self) -> list[str]:
        return [p.strip() for p in self.llm_fallback_providers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
