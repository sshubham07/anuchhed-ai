"""Application settings loaded from the environment / `.env` (spec: foundation §3.4).

Every key here mirrors `.env.example`. Model IDs, k values, thresholds and limits are config, never
literals in code (AGENTS.md rule 5, standards §2).
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["dev", "staging", "prod"]
LogFormat = Literal["console", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ModelDevice = Literal["auto", "cpu", "mps", "cuda"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----
    env: Environment = "dev"
    service_name: str = "samvidhan-api"
    app_version: str = "dev"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"
    debug_ui: bool = True
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8501"]
    )

    # ---- Database ----
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://samvidhan:samvidhan@localhost:5433/samvidhan"
    )
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_connect_timeout_s: float = Field(default=5.0, gt=0)
    readiness_timeout_s: float = Field(default=2.0, gt=0)

    # ---- LLM providers ----
    groq_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")

    # ---- Models ----
    router_model: str = "groq/llama-3.1-8b-instant"
    router_fallback_model: str = ""
    answer_model: str = "groq/llama-3.3-70b-versatile"
    answer_fallback_model: str = ""
    router_model_long: str = "groq/llama-3.3-70b-versatile"
    answer_model_long: str = ""
    answer_fallback_model_long: str = "groq/llama-3.3-70b-versatile"
    summary_model: str = "groq/llama-3.1-8b-instant"
    judge_model: str = ""
    judge_fallback_model: str = ""
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    model_device: ModelDevice = "auto"

    # ---- Daily request caps (budget guard) ----
    daily_cap_router_model: int = 14000
    daily_cap_answer_model: int = 1000
    budget_warn_ratio: float = Field(default=0.8, gt=0, le=1)

    # ---- LLM params ----
    answer_temperature: float = 0.1
    answer_max_tokens: int = 700
    answer_max_tokens_long: int = 1500
    router_max_tokens: int = 300
    llm_timeout_s: float = 20
    llm_timeout_long_s: float = 45

    # ---- Prompts ----
    router_prompt_version: str = "router.v1"
    answer_prompt_version: str = "answer.v1"
    summary_prompt_version: str = "summary.v1"
    hyde_prompt_version: str = "hyde.v1"

    # ---- Ingestion ----
    chunker_version: str = "v1"

    # ---- Retrieval ----
    dense_k: int = 20
    lexical_k: int = 20
    rrf_k: int = 60
    rerank_candidates: int = 15
    rerank_max_length: int = 512
    final_k: int = 5
    max_context_chunks: int = 8
    max_context_chunks_long: int = 15
    max_context_tokens: int = 3000
    max_context_tokens_long: int = 8000
    low_confidence_threshold: float = 0.30

    # ---- Memory ----
    router_history_messages: int = 6
    answer_history_messages: int = 2
    summary_enabled: bool = False
    raw_window: int = 12
    summary_batch: int = 6
    session_ttl_days: int = 30
    max_messages_per_session: int = 200

    # ---- Limits ----
    max_message_chars: int = 4000
    long_query_chars: int = 500
    max_article_refs: int = 10
    max_sub_queries: int = 3
    max_sub_queries_long: int = 5
    max_concurrent_streams: int = 20
    rate_limit_session: str = "10/minute"
    rate_limit_ip: str = "30/minute"
    rate_limit_ip_daily: str = "300/day"
    sessions_per_ip_daily: int = 20
    ip_hash_salt: SecretStr = SecretStr("change-me")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept `CORS_ORIGINS=a,b` as written in `.env.example`."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def database_dsn(self) -> str:
        """The DB URL as a plain string. Validated as a Postgres DSN; never log it."""
        raw = self.database_url.get_secret_value()
        PostgresDsn(raw)
        return raw


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings. Call only at composition roots (app factory, CLIs, Alembic)."""
    return Settings()
