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
    # auto: prod=migrate(Alembic), 그 외=create_all (M4-02)
    db_schema_mode: Literal["auto", "create_all", "migrate"] = "auto"

    # --- LLM (4.1) ---
    llm_default_provider: str = "fake"
    llm_fallback_providers: str = ""  # 쉼표 구분, 예: "anthropic,local"
    llm_timeout_s: float = 60.0
    # 에이전트별 프로바이더: "rca=anthropic,detection=local" (미지정 에이전트는 기본값)
    agent_llm_providers: str = ""
    # 응답 캐시 (M4-04): temperature 0 요청만, 0 = 끔
    llm_cache_ttl_s: float = 0
    llm_cache_max_entries: int = 1000
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

    # --- 운영 데이터 소스 (M2-01 / 2.1) ---
    ops_source: Literal["simulated", "live"] = "simulated"
    prometheus_url: str = "http://localhost:9090"
    prometheus_token: str | None = None
    prometheus_queries_file: Path | None = None  # {"metric": "PromQL($service,$namespace)"}
    prometheus_labels: str = "namespace=default"  # 템플릿 변수: "namespace=prod,cluster=a"
    loki_url: str = "http://localhost:3100"
    loki_selector: str = '{app="$service"}'
    loki_tenant: str | None = None
    topology_file: Path = Path("data/topology.json")

    # --- 상황 인식 (M2-05/07) ---
    situation_model_path: Path = Path("data/models/situation_lr.json")
    # 학습 확률이 이 값 미만이면 Detection 이 LLM 없이 '장애 아님' 결정 (0 = 끔).
    # 합성 평가 기준 0.05 에서 정상 알람 60% 생략·놓친 장애 0 (scripts/eval_situation.py)
    detection_triage_threshold: float = 0.0

    # --- Agent (1.x) ---
    agent_max_steps: int = 8
    memory_window: int = 20
    # 대화 컨텍스트 토큰 예산 (M4-05): 넘치면 오래된 구간을 요약. 0 = 끔(창 크기만 적용).
    # 모델 컨텍스트 한도 - 출력(max_tokens) - 도구 명세 여유분 보다 작게
    agent_context_tokens: int = 12000

    # --- 알람 → 인시던트 자동 대응 (M2-02) ---
    auto_incident_on_alert: bool = False
    auto_incident_min_severity: Literal["info", "warning", "major", "critical"] = "major"
    auto_incident_dedup_minutes: int = 30  # 같은 서비스 미해결 인시던트가 있으면 새로 만들지 않음

    # --- 사람 승인 (M3-05) ---
    approval_escalate_after_min: int = 15  # 미응답 시 에스컬레이션
    approval_timeout_min: int = 60  # 만료 시 자동 거부 (안전한 기본값)
    approval_sweep_interval_s: int = 60  # 0 = 주기 스윕 끔 (외부 cron 으로 POST /approvals/sweep)
    slack_webhook_url: str | None = None
    slack_signing_secret: str | None = None  # 상호작용 콜백 서명 검증
    slack_escalation_mention: str = "<!here>"

    # --- 조치 실행 (M3-02/06) ---
    # simulated(시뮬레이터 장애 해소) | kubernetes | dry_run(검증만, 실제 변경 금지)
    remediation_executor: Literal["simulated", "kubernetes", "dry_run"] = "simulated"
    remediation_namespace: str = "default"
    remediation_allowed_namespaces: str = "default"  # 쉼표 구분
    remediation_denied_services: str = "*-db,*database*"  # 쉼표 구분 glob
    remediation_max_actions_per_hour: int = 3
    remediation_cooldown_s: int = 300
    remediation_verify_attempts: int = 3
    remediation_verify_interval_s: float = 60.0
    k8s_api_url: str = "https://kubernetes.default.svc"
    k8s_token: str | None = None
    k8s_ca_cert: str | None = None  # 경로. 없으면 시스템 CA

    # --- API 인증 (M4-01 / 4.4) ---
    auth_mode: Literal["off", "api_key"] = "off"  # prod 에서 off 면 기동 거부
    # JSON: [{"name": "alice", "sha256": "<sha256(key)>", "roles": ["approver"]}] — 평문 키 금지.
    # 키 발급(키와 설정용 해시 출력): python -m aiops.api.auth <name> <role...>
    api_keys: str = "[]"
    # Slack 승인 허용 사용자(ID·username, 쉼표 구분). 인증이 켜져 있으면 비었을 때 아무도 불가
    slack_allowed_approvers: str = ""

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
    def prometheus_label_map(self) -> dict[str, str]:
        pairs = (p.split("=", 1) for p in self.prometheus_labels.split(",") if "=" in p)
        return {k.strip(): v.strip() for k, v in pairs}

    @property
    def guardrail_policy(self):
        from aiops.remediation.guardrails import GuardrailPolicy

        def split(v: str) -> list[str]:
            return [x.strip() for x in v.split(",") if x.strip()]

        return GuardrailPolicy(
            allowed_namespaces=split(self.remediation_allowed_namespaces),
            denied_services=split(self.remediation_denied_services),
            max_actions_per_hour=self.remediation_max_actions_per_hour,
            cooldown_s=self.remediation_cooldown_s,
        )

    @property
    def schema_mode(self) -> str:
        if self.db_schema_mode != "auto":
            return self.db_schema_mode
        return "migrate" if self.env == "prod" else "create_all"

    @property
    def slack_approvers(self) -> set[str]:
        return {x.strip() for x in self.slack_allowed_approvers.split(",") if x.strip()}

    @property
    def fallback_providers(self) -> list[str]:
        return [p.strip() for p in self.llm_fallback_providers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
