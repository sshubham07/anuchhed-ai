"""Composition root helper for the production LLM client (call once at startup)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from samvidhan.core.config import Settings
from samvidhan.llm.budget import BudgetGuard, db_usage_counter
from samvidhan.llm.client import LLMClient
from samvidhan.llm.litellm_provider import LiteLLMProvider
from samvidhan.llm.recorder import CallRecorder, DbCallRecorder, TeeRecorder


def create_llm_client(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    also_record_to: CallRecorder | None = None,
) -> LLMClient:
    """LiteLLM provider + `llm_calls` logging + daily budget guard (ADR-0009, HLD §8.6)."""
    recorder: CallRecorder = DbCallRecorder(session_factory)
    if also_record_to is not None:
        recorder = TeeRecorder(recorder, also_record_to)
    budget = BudgetGuard(
        db_usage_counter(session_factory),
        settings.daily_caps(),
        settings.budget_warn_ratio,
        cache_s=settings.budget_cache_s,
    )
    return LLMClient(
        LiteLLMProvider(settings),
        recorder,
        budget,
        max_retries=settings.llm_max_retries,
        backoff_s=settings.llm_retry_backoff_s,
    )
