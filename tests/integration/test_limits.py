"""Limit tests — one per evaluation.md §7.1 row (spec: api-sessions-memory §9.4, HLD §13.2).

Every limit is read from `Settings`, never written as a literal; where the 7-chunk fixture corpus is
too small for the default (e.g. 10 Article refs), the test lowers the setting instead.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from limits import parse
from sqlalchemy import func, select
from testcontainers.community.postgres import PostgresContainer

from samvidhan.db.models import ChatMessage
from samvidhan.generation.citations import TRUNCATION_NOTE
from samvidhan.llm.fake import FakeProvider
from samvidhan.llm.types import Completion, ProviderRequest
from tests.integration.chat_app import (
    build_app,
    capture_logs,
    chat_client,
    chat_settings,
    cite_first,
    excerpt_cites,
    log_events,
    new_session,
    parse_sse,
    query,
    running,
    stream_chat,
    user_message,
)
from tests.integration.fixture_db import migrate_and_seed

CORPUS_REFS = ["14", "21", "21A", "22", "48A"]  # every Article in the fixture corpus


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        migrate_and_seed(url)
        yield url


def route_as(**decision: Any) -> Any:
    """A router reply that echoes the message as the standalone query."""

    def reply(request: ProviderRequest) -> str:
        return json.dumps({"standalone_query": user_message(request), **decision})

    return reply


def provider(**decision: Any) -> FakeProvider:
    return FakeProvider(
        {"router": route_as(**({"type": "simple"} | decision)), "answer": cite_first}
    )


def chat_json(client: Any, session_id: str, message: str) -> Any:
    return client.post(
        "/v1/chat", json={"session_id": session_id, "message": message, "stream": False}
    )


def message_of(length: int) -> str:
    return ("What does Article 21 say about personal liberty? " * (length // 20 + 1))[:length]


def _llm_call_count(database_url: str) -> int:
    return int(query(database_url, "SELECT count(*) FROM llm_calls")[0][0])


# ---- Input ----


def test_max_length_message_is_accepted(database_url: str) -> None:
    settings = chat_settings(database_url)
    fake = provider()
    with chat_client(settings, fake) as (client, _):
        response = chat_json(client, new_session(client), message_of(settings.max_message_chars))
    assert response.status_code == 200
    assert [c.purpose for c in fake.calls] == ["router", "answer"]


def test_over_length_message_is_rejected_without_llm_call(database_url: str) -> None:
    settings = chat_settings(database_url)
    fake = provider()
    before = _llm_call_count(database_url)
    with chat_client(settings, fake) as (client, logs):
        session_id = new_session(client)
        response = chat_json(client, session_id, message_of(settings.max_message_chars + 1))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MESSAGE_TOO_LONG"
    assert f"the limit is {settings.max_message_chars}" in error["message"]
    assert fake.calls == [] and _llm_call_count(database_url) == before
    [rejected] = log_events(logs, "input_rejected")
    assert rejected["reason"] == "too_long" and rejected["limit"] == settings.max_message_chars


@pytest.mark.parametrize("message", ["", "   \n"])
def test_empty_message_is_rejected_without_llm_call(database_url: str, message: str) -> None:
    fake = provider()
    with chat_client(chat_settings(database_url), fake) as (client, logs):
        response = chat_json(client, new_session(client), message)
    assert response.status_code == 422 and response.json()["error"]["code"] == "EMPTY_MESSAGE"
    assert fake.calls == []
    assert log_events(logs, "input_rejected")[0]["reason"] == "empty"


def test_long_query_switches_the_router_model(database_url: str) -> None:
    settings = chat_settings(database_url)
    with chat_client(settings, provider()) as (client, _):
        session_id = new_session(client)
        ids = []
        for length in (settings.long_query_chars, settings.long_query_chars + 1):
            response = chat_json(client, session_id, message_of(length))
            assert response.status_code == 200
            ids.append(response.headers["X-Request-ID"])
    models = [
        query(
            database_url,
            "SELECT model FROM llm_calls WHERE request_id = :rid AND purpose = 'router'",
            rid=rid,
        )[0][0]
        for rid in ids
    ]
    assert models == [settings.router_model, settings.router_model_long]


def test_sub_query_cap(database_url: str) -> None:
    settings = chat_settings(database_url)
    sub_queries = [f"part {n} of the question about Article 21" for n in range(8)]
    fake = provider(type="multi_part", sub_queries=sub_queries)
    with chat_client(settings, fake) as (client, logs):
        response = chat_json(client, new_session(client), message_of(settings.long_query_chars + 1))
    assert response.status_code == 200
    assert len(log_events(logs, "retrieval_completed")) == settings.max_sub_queries_long
    [limit] = [e for e in log_events(logs, "limit_applied") if e["limit"] == "sub_queries"]
    assert (limit["requested"], limit["allowed"]) == (8, settings.max_sub_queries_long)


def test_article_ref_cap_notes_the_skipped_refs(database_url: str) -> None:
    cap = 3
    settings = chat_settings(database_url, max_article_refs=cap)
    fake = provider(type="article_lookup", article_refs=CORPUS_REFS)
    with chat_client(settings, fake) as (client, logs):
        session_id = new_session(client)
        events = stream_chat(client, session_id, "Compare Articles 14, 21, 21A, 22 and 48A")
    done = events[-1][1]
    trace = query(
        database_url,
        "SELECT retrieval_trace FROM chat_messages WHERE id = :id",
        id=done["message_id"],
    )[0][0]
    assert trace["refs"] == CORPUS_REFS[:cap]
    assert f"first {cap} provisions" in done["answer"]
    assert "Art. 22, Art. 48A separately" in done["answer"]
    streamed = "".join(data["text"] for name, data in events if name == "token")
    assert streamed == done["answer"]  # the note is streamed too
    [limit] = [e for e in log_events(logs, "limit_applied") if e["limit"] == "article_refs"]
    assert (limit["requested"], limit["allowed"]) == (len(CORPUS_REFS), cap)


# ---- Retrieval and context ----


def test_context_chunk_cap_keeps_pinned_first(database_url: str) -> None:
    settings = chat_settings(database_url, max_context_chunks=2)
    fake = provider(article_refs=["48A"])
    with chat_client(settings, fake) as (client, logs):
        response = chat_json(client, new_session(client), "the State and the environment")
    assert response.status_code == 200
    [answer_call] = [c for c in fake.calls if c.purpose == "answer"]
    cites = excerpt_cites(answer_call)
    assert len(cites) == settings.max_context_chunks and cites[0] == "Art. 48A"
    assert any(e["limit"] == "context_chunks" for e in log_events(logs, "limit_applied"))


def test_context_token_cap(database_url: str) -> None:
    settings = chat_settings(database_url, max_context_tokens=10)  # below one fixture chunk
    fake = provider(article_refs=["21"])
    with chat_client(settings, fake) as (client, logs):
        response = chat_json(client, new_session(client), "personal liberty")
    assert response.status_code == 200
    [answer_call] = [c for c in fake.calls if c.purpose == "answer"]
    assert excerpt_cites(answer_call) == ["Art. 21"]  # the first (pinned) chunk always fits
    assert any(e["limit"] == "context_tokens" for e in log_events(logs, "limit_applied"))


# ---- Generation ----


def test_answer_cap_adds_the_truncation_note(database_url: str) -> None:
    settings = chat_settings(database_url)
    fake = provider(article_refs=["21"])
    fake.finish_reason = {"answer": "length"}
    with chat_client(settings, fake) as (client, logs):
        response = chat_json(client, new_session(client), "What does Article 21 say?")
    assert TRUNCATION_NOTE in response.json()["answer"]
    [truncated] = log_events(logs, "answer_truncated")
    assert truncated["max_tokens"] == settings.answer_max_tokens


def test_timeout_retries_then_falls_back_then_fails(database_url: str) -> None:
    settings = chat_settings(database_url, llm_timeout_s=0.05)
    fake = provider(article_refs=["21"])
    fake.delay_s = {"answer": settings.llm_timeout_s * 10}
    with chat_client(settings, fake) as (client, _):
        session_id = new_session(client)
        events = stream_chat(client, session_id, "What does Article 21 say?")
        rid = events[0][1]["request_id"]
    assert events[-1][0] == "error" and events[-1][1]["code"] == "LLM_UNAVAILABLE"
    calls = query(
        database_url,
        "SELECT model, status, error_code FROM llm_calls "
        "WHERE request_id = :rid AND purpose = 'answer' ORDER BY id",
        rid=rid,
    )
    # 1 retry per model (LLM_MAX_RETRIES), then the fallback model, all timing out
    attempts = 1 + settings.llm_max_retries
    assert [c.model for c in calls] == (
        [settings.answer_model] * attempts + [settings.answer_fallback_model] * attempts
    )
    assert {(c.status, c.error_code) for c in calls} == {("error", "timeout")}


# ---- Conversation and traffic ----


def test_session_message_cap(database_url: str) -> None:
    settings = chat_settings(database_url, max_messages_per_session=2)
    fake = provider(type="chitchat")
    with chat_client(settings, fake) as (client, logs):
        session_id = new_session(client)
        assert chat_json(client, session_id, "hello").status_code == 200
        n_calls = len(fake.calls)
        full = chat_json(client, session_id, "hello again")
    assert full.status_code == 409 and full.json()["error"]["code"] == "SESSION_FULL"
    assert "new chat" in full.json()["error"]["message"]
    assert len(fake.calls) == n_calls
    assert log_events(logs, "input_rejected")[-1]["reason"] == "session_full"


def test_session_rate_limit(database_url: str) -> None:
    settings = chat_settings(database_url, rate_limit_session="2/minute")
    allowed = parse(settings.rate_limit_session).amount
    with chat_client(settings, provider(type="chitchat")) as (client, _):
        session_id = new_session(client)
        codes = [chat_json(client, session_id, "hello").status_code for _ in range(allowed)]
        limited = chat_json(client, session_id, "hello")
    assert codes == [200] * allowed
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) > 0


@dataclass
class GatedProvider(FakeProvider):
    """Answers block until `gate` is set, so streams stay open as long as the test needs."""

    gate: asyncio.Event = field(default_factory=asyncio.Event)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[str | Completion]:
        if request.purpose == "answer":
            await self.gate.wait()
        async for item in super().stream(request):
            yield item


def test_concurrent_stream_cap(database_url: str) -> None:
    settings = chat_settings(database_url, max_concurrent_streams=1)

    async def run() -> None:
        fake = GatedProvider({"router": route_as(type="simple", article_refs=["21"]),
                              "answer": cite_first})  # fmt: skip
        app = build_app(settings, fake)
        logs = capture_logs(settings)
        async with (
            running(app) as services,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=10
            ) as http,
        ):
            session_ids = [(await http.post("/v1/sessions")).json()["session_id"] for _ in range(2)]

            def chat(session_id: str, stream: bool) -> Any:
                body = {"session_id": session_id, "message": "Article 21?", "stream": stream}
                return http.post("/v1/chat", json=body)

            open_streams = [asyncio.create_task(chat(session_ids[0], stream=True))]
            deadline = time.monotonic() + 5
            # the first stream holds its slot, past the router, waiting on the answer gate
            while not any(c.purpose == "router" for c in fake.calls):
                assert time.monotonic() < deadline, "the first stream never reached the router"
                await asyncio.sleep(0.01)
            assert services.streams.in_use == settings.max_concurrent_streams
            n_calls = len(fake.calls)

            extra = await chat(session_ids[1], stream=False)  # MAX_CONCURRENT_STREAMS + 1
            assert extra.status_code == 503 and extra.json()["error"]["code"] == "BUSY"
            assert len(fake.calls) == n_calls  # rejected before the graph ran
            assert await _user_messages(services, session_ids[1]) == 0

            fake.gate.set()
            first = await open_streams[0]
            assert first.status_code == 200 and parse_sse(first.text)[-1][0] == "done"
            assert services.streams.in_use == 0  # freed when the stream ended
            again = await chat(session_ids[1], stream=False)
            assert again.status_code == 200 and services.streams.in_use == 0
        [limit] = [
            e for e in log_events(logs, "limit_applied") if e["limit"] == "concurrent_streams"
        ]
        assert limit["allowed"] == settings.max_concurrent_streams

    asyncio.run(run())


async def _user_messages(services: Any, session_id: str) -> int:
    async with services.session_factory() as db:
        result = await db.execute(
            select(func.count()).where(ChatMessage.session_id == uuid.UUID(session_id))
        )
        return int(result.scalar_one())
