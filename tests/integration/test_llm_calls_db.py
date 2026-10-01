"""`llm_calls` against a real Postgres: migration 003, one row per attempt, budget counts
(spec: llm-router-generation §6, observability §3.5)."""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
import structlog.testing
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from samvidhan.db.engine import create_session_factory
from samvidhan.llm.budget import BudgetGuard, db_usage_counter
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.recorder import DbCallRecorder
from samvidhan.llm.types import LLMRequest
from tests.integration.fixture_db import alembic_config

PRIMARY, FALLBACK = "groq/llama-3.3-70b-versatile", "gemini/gemini-flash"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        command.upgrade(alembic_config(url), "head")
        yield url


def _sql(url: str, statement: str, **params: Any) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return list(result) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def request() -> LLMRequest:
    return LLMRequest(
        purpose="answer",
        models=[PRIMARY, FALLBACK],
        messages=[{"role": "user", "content": "q"}],
        max_tokens=50,
        temperature=0.1,
        timeout_s=5,
        prompt_version="answer.v1",
    )


def test_migration_003_up_and_down(database_url: str) -> None:
    indexes = {r[0] for r in _sql(database_url, "SELECT indexname FROM pg_indexes "
                                               "WHERE tablename = 'llm_calls'")}  # fmt: skip
    assert indexes == {"pk_llm_calls", "ix_llm_calls_created_at", "ix_llm_calls_model_created_at"}
    checks = {r[0] for r in _sql(database_url, "SELECT conname FROM pg_constraint "
                                              "WHERE conrelid = 'llm_calls'::regclass "
                                              "AND contype = 'c'")}  # fmt: skip
    assert checks == {"ck_llm_calls_purpose", "ck_llm_calls_status"}

    command.downgrade(alembic_config(database_url), "002")
    tables = {r[0] for r in _sql(database_url, "SELECT tablename FROM pg_tables")}
    assert "llm_calls" not in tables and "chunks" in tables
    command.upgrade(alembic_config(database_url), "head")


def test_every_attempt_writes_one_row(database_url: str) -> None:
    """Bad primary key → fallback: two rows, same request id, statuses error then fallback."""
    request_id, session_id = f"req-{uuid.uuid4().hex[:8]}", uuid.uuid4()

    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            recorder = DbCallRecorder(create_session_factory(engine))
            provider = FakeProvider({"answer": "ok [Art. 21]"}, fail_models={PRIMARY: "auth"})
            structlog.contextvars.bind_contextvars(
                request_id=request_id, session_id=str(session_id)
            )
            result = await fake_llm(provider, recorder).complete(request())
            assert result.model == FALLBACK
            await recorder.drain()
        finally:
            await engine.dispose()

    asyncio.run(run())
    rows = _sql(
        database_url,
        "SELECT model, provider, status, error_code, purpose, prompt_version, session_id, "
        "input_tokens IS NOT NULL, latency_ms IS NOT NULL FROM llm_calls "
        "WHERE request_id = :request_id ORDER BY id",
        request_id=request_id,
    )
    assert [tuple(r) for r in rows] == [
        (PRIMARY, "groq", "error", "auth", "answer", "answer.v1", session_id, False, True),
        (FALLBACK, "gemini", "fallback", None, "answer", "answer.v1", session_id, True, True),
    ]


def test_budget_counts_todays_rows(database_url: str) -> None:
    model = f"groq/budget-{uuid.uuid4().hex[:6]}"
    _sql(
        database_url,
        "INSERT INTO llm_calls (purpose, provider, model, status, created_at) VALUES "
        "('router', 'groq', :model, 'ok', now()), "
        "('router', 'groq', :model, 'error', now()), "
        "('router', 'groq', :model, 'ok', now() - interval '2 days')",
        model=model,
    )

    async def run() -> tuple[int, bool, bool]:
        engine = create_async_engine(database_url)
        try:
            counter = db_usage_counter(create_session_factory(engine))
            used = await counter(model)
            under = await BudgetGuard(counter, {model: 10}, warn_ratio=0.8).allows(model)
            over = await BudgetGuard(counter, {model: 2}, warn_ratio=0.8).allows(model)
            return used, under, over
        finally:
            await engine.dispose()

    assert asyncio.run(run()) == (2, True, False)  # yesterday's row doesn't count


def test_record_failure_is_logged_not_raised() -> None:
    async def run() -> list[dict[str, Any]]:
        engine = create_async_engine("postgresql+asyncpg://x:x@127.0.0.1:1/x")
        try:
            recorder = DbCallRecorder(create_session_factory(engine))
            with structlog.testing.capture_logs() as logs:
                result = await fake_llm(FakeProvider({"answer": "fine"}), recorder).complete(
                    request()
                )
                await recorder.drain()
            assert result.text == "fine"
            return logs
        finally:
            await engine.dispose()

    logs = asyncio.run(run())
    assert any(e["event"] == "llm_call_record_failed" for e in logs)
