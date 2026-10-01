"""Log completeness (spec: api-sessions-memory §9.3, observability §1.2–1.3): one chat request emits
every pipeline stage event, and every line it emits carries the same `request_id`."""

import json
from collections.abc import Iterator

import pytest
from testcontainers.community.postgres import PostgresContainer

from samvidhan.llm.fake import FakeProvider
from tests.integration.chat_app import (
    chat_client,
    chat_settings,
    cite_first,
    log_events,
    new_session,
    query,
    stream_chat,
)
from tests.integration.fixture_db import migrate_and_seed

REQUEST_ID = "log-completeness-0001"
STAGE_EVENTS = {
    "chat_request_received",
    "memory_loaded",
    "router_completed",
    "retrieval_completed",
    "rerank_completed",
    "llm_call_completed",
    "answer_completed",
    "http_request_completed",
}
# Emitted once the session is known (`/v1/chat` binds it before any of these).
SESSION_EVENTS = STAGE_EVENTS - {"http_request_completed"}


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        migrate_and_seed(url)
        yield url


def test_one_request_logs_every_stage_with_its_request_id(database_url: str) -> None:
    router = json.dumps(
        {"type": "article_lookup", "standalone_query": "What does Article 21 say?",
         "article_refs": ["21"]}
    )  # fmt: skip
    fake = FakeProvider({"router": router, "answer": cite_first})
    with chat_client(chat_settings(database_url), fake) as (client, logs):
        session_id = new_session(client)
        logs.seek(0)
        logs.truncate()  # only the chat request from here on
        events = stream_chat(
            client, session_id, "What does Article 21 say?", headers={"X-Request-ID": REQUEST_ID}
        )
        # The TestClient's own httpx line is client-side, not part of the request.
        lines = [
            line for line in log_events(logs) if not line.get("logger", "").startswith("httpx")
        ]
    assert events[-1][0] == "done"

    names = {line["event"] for line in lines}
    assert names >= STAGE_EVENTS, f"missing: {STAGE_EVENTS - names}"
    assert [line["purpose"] for line in lines if line["event"] == "llm_call_completed"] == [
        "router",
        "answer",
    ]
    strays = [line["event"] for line in lines if line.get("request_id") != REQUEST_ID]
    assert strays == [], f"lines without the request id: {strays}"
    for line in lines:
        if line["event"] in SESSION_EVENTS:
            assert line.get("session_id") == session_id, line["event"]
        assert {"timestamp", "level", "service", "env", "version"} <= set(line)

    calls = query(
        database_url,
        "SELECT purpose, session_id FROM llm_calls WHERE request_id = :rid ORDER BY id",
        rid=REQUEST_ID,
    )
    assert [c.purpose for c in calls] == ["router", "answer"]
    assert {str(c.session_id) for c in calls} == {session_id}
