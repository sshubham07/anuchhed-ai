"""Session expiry + feedback anonymization (spec: api-sessions-memory §9.1)."""

import asyncio
import io
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from samvidhan.core.config import Settings
from samvidhan.core.logging import configure_logging
from samvidhan.db.engine import create_session_factory
from samvidhan.db.models import ChatSession
from samvidhan.db.repositories.chat import (
    FeedbackRepository,
    MessageRepository,
    NewAssistantMessage,
    SessionRepository,
)
from samvidhan.ops.cleanup import expire_sessions
from tests.integration.fixture_db import migrate_and_seed

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        migrate_and_seed(url)
        yield url


async def _session_with_feedback(
    factory: async_sessionmaker[AsyncSession], idle_days: int, *, snapshot: bool = True
) -> uuid.UUID:
    session_id = uuid.uuid4()
    async with factory() as db, db.begin():
        await SessionRepository(db).create(session_id, "hash")
        messages = MessageRepository(db)
        question = await messages.add_user(session_id, "req", "What does Article 21 say?")
        answer = await messages.add_assistant(
            NewAssistantMessage(session_id, "req", "Life and liberty [Art. 21].", None, None,
                                ["21"], None, None, {})
        )  # fmt: skip
        await FeedbackRepository(db).add(
            answer, question.content if snapshot else None, 1, "useful"
        )
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(last_active_at=NOW - timedelta(days=idle_days))
        )
    return session_id


def test_expiry_deletes_idle_sessions_and_keeps_anonymized_feedback(
    database_url: str, settings: Settings
) -> None:
    logs = io.StringIO()
    configure_logging(settings, stream=logs)
    ttl = settings.session_ttl_days

    async def run() -> None:
        engine = create_async_engine(database_url)
        factory = create_session_factory(engine)
        try:
            expired = [
                await _session_with_feedback(factory, ttl + 1),
                await _session_with_feedback(factory, ttl + 5, snapshot=False),
                await _session_with_feedback(factory, ttl + 9),
            ]
            kept = await _session_with_feedback(factory, ttl - 1)

            dry = await expire_sessions(factory, ttl_days=ttl, batch_size=2, dry_run=True, now=NOW)
            assert (dry.sessions, dry.messages, dry.feedback) == (3, 6, 3)
            async with engine.connect() as conn:
                assert (
                    await conn.execute(text("SELECT count(*) FROM chat_sessions"))
                ).scalar() == 4

            # batch_size=2 → two batches; the total covers both
            done = await expire_sessions(factory, ttl_days=ttl, batch_size=2, now=NOW)
            assert (done.sessions, done.messages, done.feedback) == (3, 6, 3)

            async with engine.connect() as conn:
                sessions = {r[0] for r in await conn.execute(text("SELECT id FROM chat_sessions"))}
                assert sessions == {kept}
                orphans = await conn.execute(
                    text("SELECT count(*) FROM chat_messages WHERE session_id = ANY(:ids)"),
                    {"ids": expired},
                )
                assert orphans.scalar() == 0
                rows = list(
                    await conn.execute(
                        text("SELECT message_id, question, answer, comment FROM feedback")
                    )
                )
            assert len(rows) == 4  # every feedback row survives
            unlinked = [r for r in rows if r.message_id is None]
            assert len(unlinked) == 3
            for row in unlinked:  # the missing snapshot was filled before the delete
                assert row.question == "What does Article 21 say?"
                assert row.answer == "Life and liberty [Art. 21]."
                assert row.comment == "useful"
        finally:
            await engine.dispose()

    asyncio.run(run())
    events = [json.loads(line) for line in logs.getvalue().splitlines() if line.strip()]
    expiry = [e for e in events if e["event"] == "sessions_expired"]
    assert [e["dry_run"] for e in expiry] == [True, False]
    assert expiry[-1]["n_sessions"] == 3
    assert expiry[-1]["n_feedback_anonymized"] == 3
