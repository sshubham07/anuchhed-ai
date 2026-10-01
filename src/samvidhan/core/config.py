"""Application settings loaded from the environment / `.env` (spec: foundation §3.4).

Every key here mirrors `.env.example`. Model IDs, k values, thresholds and limits are config, never
literals in code (AGENTS.md rule 5, standards §2).
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
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
    database_url: SecretStr  # required, from env only (no credentialed default)
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
    daily_cap_router_fallback_model: int = Field(default=0, ge=0)  # 0 = no cap
    daily_cap_answer_fallback_model: int = Field(default=0, ge=0)
    budget_warn_ratio: float = Field(default=0.8, gt=0, le=1)
    budget_cache_s: float = Field(default=30, ge=0)

    # ---- LLM params ----
    answer_temperature: float = 0.1
    answer_max_tokens: int = 700
    answer_max_tokens_long: int = 1500
    router_max_tokens: int = 300
    router_temperature: float = 0.0
    hyde_max_tokens: int = 200
    llm_timeout_s: float = 20
    llm_timeout_long_s: float = 45
    llm_max_retries: int = Field(default=1, ge=0)
    llm_retry_backoff_s: float = Field(default=0.5, ge=0)

    # ---- Prompts ----
    router_prompt_version: str = "router.v1"
    answer_prompt_version: str = "answer.v1"
    summary_prompt_version: str = "summary.v1"
    hyde_prompt_version: str = "hyde.v1"
    prompts_dir: Path = Path("prompts")

    # ---- Ingestion ----
    chunker_version: str = "v1"
    embed_batch_size: int = Field(default=16, ge=1)
    embed_max_length: int = Field(default=1024, ge=16)
    ingest_min_text_page_ratio: float = Field(default=0.9, gt=0, le=1)
    chunk_max_tokens: int = Field(default=800, ge=100)
    chunk_target_min_tokens: int = Field(default=300, ge=1)
    chunk_target_max_tokens: int = Field(default=700, ge=50)
    schedule7_entries_per_chunk: int = Field(default=10, ge=1)

    # ---- Retrieval ----
    dense_k: int = 20
    lexical_k: int = 20
    rrf_k: int = 60
    rerank_candidates: int = 15
    rerank_max_length: int = 512
    final_k: int = 5
    sub_query_k: int = Field(default=3, ge=1)  # chunks kept per sub-query (multi_part)
    max_context_chunks: int = 8
    max_context_chunks_long: int = 15
    max_context_tokens: int = 3000
    max_context_tokens_long: int = 8000
    low_confidence_threshold: float = 0.05  # tuned on dev (P3.8)

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

    @model_validator(mode="after")
    def _chunk_limits_are_ordered(self) -> "Settings":
        """min < max ≤ split threshold ≤ embedder limit, or chunks get oddly split / truncated."""
        if not (
            self.chunk_target_min_tokens
            < self.chunk_target_max_tokens
            <= self.chunk_max_tokens
            <= self.embed_max_length
        ):
            raise ValueError(
                "need CHUNK_TARGET_MIN_TOKENS < CHUNK_TARGET_MAX_TOKENS <= CHUNK_MAX_TOKENS"
                " <= EMBED_MAX_LENGTH"
            )
        return self

    def daily_caps(self) -> dict[str, int]:
        """Daily request cap per model (budget guard); a model in two roles gets the lower cap."""
        caps: dict[str, int] = {}
        for model, cap in (
            (self.router_model, self.daily_cap_router_model),
            (self.answer_model, self.daily_cap_answer_model),
            (self.router_fallback_model, self.daily_cap_router_fallback_model),
            (self.answer_fallback_model, self.daily_cap_answer_fallback_model),
        ):
            if model and cap > 0:
                caps[model] = min(cap, caps.get(model, cap))
        return caps

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
