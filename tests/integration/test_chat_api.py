"""The `/v1` API over a real Postgres (spec: api-sessions-memory §6): sessions, SSE chat with
Postgres-backed memory, JSON mode, history, feedback, articles, meta.

Real retrieval (FakeEmbedder + FakeReranker), real `llm_calls` rows; only the model is fake.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from samvidhan.api.main import create_app
from samvidhan.core.config import Settings
from samvidhan.db.engine import create_session_factory
from samvidhan.llm.fake import FakeProvider
from samvidhan.llm.recorder import DbCallRecorder
from samvidhan.llm.types import ProviderRequest
from samvidhan.retrieval.rerank import FakeReranker
from samvidhan.retrieval.service import RetrievalService
from tests.api_helpers import fake_services
from tests.integration.fixture_db import EMBEDDER, migrate_and_seed

_MESSAGE = re.compile(r"<message>\n(.*?)\n</message>", re.S)
_CITE = re.compile(r'cite="([^"]+)"')

ROUTER_PROMPTS: list[str] = []


def router(request: ProviderRequest) -> str:
    prompt = request.messages[-1]["content"]
    ROUTER_PROMPTS.append(prompt)
    match = _MESSAGE.search(prompt)
    assert match, "router prompt must carry the message"
    message = match.group(1)
    if message == "What are its exceptions?":
        # A real router resolves "its" from memory; the fake only answers when memory is there.
        if "last_articles: 21" not in prompt:
            return json.dumps({"type": "ambiguous", "standalone_query": message})
        return json.dumps(
            {
                "type": "simple",
                "standalone_query": "exceptions to Article 21 personal liberty",
                "article_refs": ["21"],
            }
        )
    if message == "thanks!":
        return json.dumps({"type": "chitchat", "standalone_query": message})
    return json.dumps(
        {"type": "article_lookup", "standalone_query": "What does Article 21 say?",
         "article_refs": ["21"]}
    )  # fmt: skip


def answer(request: ProviderRequest) -> str:
    """Cites only the first excerpt, so `last_articles` is exactly the pinned Article."""
    cites = _CITE.findall(request.messages[-1]["content"])
    return f"The provision applies [{cites[0]}]."


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        migrate_and_seed(url)
        yield url


def _settings(database_url: str, **overrides: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=SecretStr(database_url),
        rate_limit_ip="1000/minute",
        rate_limit_session="1000/minute",
        **overrides,
    )


def _app_client(settings: Settings) -> TestClient:
    async def factory(s: Settings) -> Any:
        from samvidhan.db.engine import create_engine

        session_factory = create_session_factory(create_engine(s))
        provider = FakeProvider({"router": router, "answer": answer})
        retrieval = RetrievalService(session_factory, EMBEDDER, FakeReranker(), s)
        return await fake_services(provider, retrieval, DbCallRecorder(session_factory))(s)

    return TestClient(create_app(settings, factory))


@pytest.fixture(scope="module")
def client(database_url: str) -> Iterator[TestClient]:
    with _app_client(_settings(database_url)) as test_client:
        yield test_client


def _query(database_url: str, sql: str, **params: Any) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return list(await conn.execute(text(sql), params))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _new_session(client: TestClient) -> str:
    response = client.post("/v1/sessions")
    assert response.status_code == 201
    return str(response.json()["session_id"])


def _stream(client: TestClient, session_id: str, message: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    body = {"session_id": session_id, "message": message}
    with client.stream("POST", "/v1/chat", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = "".join(response.iter_text())
    for block in raw.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def test_session_lifecycle(client: TestClient) -> None:
    session_id = _new_session(client)
    assert uuid.UUID(session_id).version == 7
    assert client.get(f"/v1/sessions/{session_id}/messages").json() == {
        "messages": [],
        "next_before": None,
    }
    assert client.delete(f"/v1/sessions/{session_id}").status_code == 204
    assert client.delete(f"/v1/sessions/{session_id}").status_code == 404
    missing = client.get(f"/v1/sessions/{session_id}/messages")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_follow_up_resolves_last_articles_from_postgres(
    client: TestClient, database_url: str
) -> None:
    session_id = _new_session(client)
    first = _stream(client, session_id, "What does Article 21 say?")
    names = [name for name, _ in first]
    assert names[0] == "meta" and names[-2:] == ["citations", "done"]
    assert set(names[1:-2]) == {"token"}
    assert first[0][1]["route_type"] == "article_lookup"
    assert [c["ref"] for c in first[-2][1]["citations"]] == ["21"]

    ROUTER_PROMPTS.clear()
    second = _stream(client, session_id, "What are its exceptions?")
    meta, done = second[0][1], second[-1][1]
    assert "last_articles: 21" in ROUTER_PROMPTS[0]
    history = ROUTER_PROMPTS[0].split("<history>")[1].split("</history>")[0]
    assert "What does Article 21 say?" in history  # loaded from chat_messages
    assert "What are its exceptions?" not in history  # the current question is not its own history
    assert meta["route_type"] == "simple" and meta["refs"] == ["21"]
    assert "Article 21" in meta["standalone_query"]
    streamed = "".join(data["text"] for name, data in second if name == "token")
    assert streamed == done["answer"]
    assert {"route", "retrieve", "generate", "ttft", "total"} <= set(done["latency_ms"])

    [memory] = _query(
        database_url, "SELECT memory FROM chat_sessions WHERE id = :id", id=session_id
    )
    assert memory[0] == {
        "last_articles": ["21"],
        "articles_discussed": ["21"],
        "parts_discussed": ["III"],
        "recent_topics": [
            "What does Article 21 say?",
            "exceptions to Article 21 personal liberty",
        ],
    }
    rows = _query(
        database_url,
        "SELECT id, role, request_id, route, cited_articles, retrieval_trace, prompt_version, "
        "latency_ms FROM chat_messages WHERE session_id = :id ORDER BY id",
        id=session_id,
    )
    assert [r.role for r in rows] == ["user", "assistant", "user", "assistant"]
    assistant = rows[3]
    assert assistant.id == done["message_id"]
    assert assistant.route["type"] == "simple" and assistant.cited_articles == ["21"]
    assert assistant.retrieval_trace["refs"] == ["21"]
    assert assistant.prompt_version == "answer.v1" and "total" in assistant.latency_ms
    assert rows[2].request_id == assistant.request_id  # user + answer share the request id

    calls = _query(
        database_url,
        "SELECT purpose, session_id FROM llm_calls WHERE request_id = :rid ORDER BY id",
        rid=assistant.request_id,
    )
    assert [c.purpose for c in calls] == ["router", "answer"]
    assert {str(c.session_id) for c in calls} == {session_id}


def test_template_reply_streams_and_keeps_memory(client: TestClient, database_url: str) -> None:
    session_id = _new_session(client)
    _stream(client, session_id, "What does Article 21 say?")
    events = _stream(client, session_id, "thanks!")
    assert [name for name, _ in events] == ["meta", "token", "citations", "done"]
    [memory] = _query(
        database_url, "SELECT memory FROM chat_sessions WHERE id = :id", id=session_id
    )
    assert memory[0]["last_articles"] == ["21"]  # chitchat keeps the follow-up anchor


def test_json_mode_and_history_pages(client: TestClient) -> None:
    session_id = _new_session(client)
    body = {"session_id": session_id, "message": "What does Article 21 say?", "stream": False}
    response = client.post("/v1/chat", json=body)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "message_id", "answer", "citations", "route", "low_confidence", "latency_ms"
    }  # fmt: skip
    assert data["citations"][0] == {
        "ref": "21", "label": "Art. 21", "title": "Protection of life and personal liberty"
    }  # fmt: skip
    assert data["route"]["type"] == "article_lookup" and isinstance(data["message_id"], int)
    client.post("/v1/chat", json={**body, "message": "thanks!"})

    page = client.get(f"/v1/sessions/{session_id}/messages", params={"limit": 3}).json()
    assert [m["role"] for m in page["messages"]] == ["assistant", "user", "assistant"]
    assert page["messages"][0]["id"] == data["message_id"]
    assert page["messages"][0]["cited_articles"] == ["21"]
    older = client.get(
        f"/v1/sessions/{session_id}/messages", params={"before": page["next_before"]}
    ).json()
    assert [m["content"] for m in older["messages"]] == ["What does Article 21 say?"]
    assert older["next_before"] is None


def test_feedback_snapshot_survives_session_delete(client: TestClient, database_url: str) -> None:
    session_id = _new_session(client)
    body = {"session_id": session_id, "message": "What does Article 21 say?", "stream": False}
    message_id = client.post("/v1/chat", json=body).json()["message_id"]
    response = client.post(
        f"/v1/messages/{message_id}/feedback", json={"rating": -1, "comment": "x"}
    )
    assert response.status_code == 201
    feedback_id = response.json()["feedback_id"]
    assert (
        client.post(f"/v1/messages/{message_id - 1}/feedback", json={"rating": 1}).status_code
        == 404
    )
    assert client.post(f"/v1/messages/{message_id}/feedback", json={"rating": 5}).status_code == 422

    assert client.delete(f"/v1/sessions/{session_id}").status_code == 204
    [row] = _query(
        database_url,
        "SELECT message_id, rating, question, answer FROM feedback WHERE id = :id",
        id=feedback_id,
    )
    assert row.message_id is None and row.rating == -1
    assert row.question == "What does Article 21 say?" and row.answer.startswith("The provision")
    remaining = _query(
        database_url, "SELECT count(*) FROM chat_messages WHERE session_id = :id", id=session_id
    )
    assert remaining[0][0] == 0


def test_chat_errors(client: TestClient, database_url: str) -> None:
    unknown = {"session_id": str(uuid.uuid4()), "message": "hi"}
    response = client.post("/v1/chat", json=unknown)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"

    with _app_client(_settings(database_url, max_messages_per_session=2)) as small:
        session_id = _new_session(small)
        body = {"session_id": session_id, "message": "thanks!", "stream": False}
        assert small.post("/v1/chat", json=body).status_code == 200
        full = small.post("/v1/chat", json=body)
    assert full.status_code == 409 and full.json()["error"]["code"] == "SESSION_FULL"


def test_sessions_per_ip_per_day(database_url: str) -> None:
    settings = _settings(
        database_url, sessions_per_ip_daily=2, ip_hash_salt=SecretStr(uuid.uuid4().hex)
    )
    with _app_client(settings) as fresh_ip:
        codes = [fresh_ip.post("/v1/sessions").status_code for _ in range(3)]
        limited = fresh_ip.post("/v1/sessions")
    assert codes == [201, 201, 429]
    assert limited.json()["error"]["code"] == "RATE_LIMITED" and "Retry-After" in limited.headers


def test_articles_meta_and_readyz(client: TestClient) -> None:
    article = client.get("/v1/articles/21A").json()
    assert client.get("/v1/articles/21-A").json() == article
    assert article["ref"] == "21A" and article["label"] == "Art. 21A"
    assert article["title"] == "Right to education" and article["part_no"] == "III"
    assert article["chunk_ids"] == ["art-21A#0"] and "free and compulsory" in article["text"]
    assert client.get("/v1/articles/preamble").json()["label"] == "Preamble"
    missing = client.get("/v1/articles/999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"

    meta = client.get("/v1/meta").json()
    assert meta["edition_date"] == "2024-05-01" and meta["prompt_versions"]["answer"] == "answer.v1"
    assert not any("key" in field.lower() for field in meta)

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "active_document": "ok", "models": "ok"}
