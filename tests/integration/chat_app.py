"""The real `/v1` app over a test Postgres: real retrieval (FakeEmbedder + FakeReranker), real
`llm_calls` rows, Postgres memory; only the model is a `FakeProvider`. Logs are captured as JSON.
"""

import asyncio
import io
import json
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from samvidhan.api.main import create_app
from samvidhan.api.services import AppServices
from samvidhan.core.config import Settings
from samvidhan.core.logging import configure_logging
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.llm.fake import FakeProvider
from samvidhan.llm.recorder import DbCallRecorder
from samvidhan.llm.types import ProviderRequest
from samvidhan.retrieval.rerank import FakeReranker
from samvidhan.retrieval.service import RetrievalService
from tests.api_helpers import fake_services
from tests.integration.fixture_db import EMBEDDER

_MESSAGE = re.compile(r"<message>\n(.*?)\n</message>", re.S)
_CITE = re.compile(r'cite="([^"]+)"')


def user_message(request: ProviderRequest) -> str:
    """The user's message inside a router prompt."""
    match = _MESSAGE.search(request.messages[-1]["content"])
    assert match, "router prompt must carry the message"
    return match.group(1)


def excerpt_cites(request: ProviderRequest) -> list[str]:
    """`cite` labels of the excerpts in an answer prompt, in order."""
    return _CITE.findall(request.messages[-1]["content"])


def cite_first(request: ProviderRequest) -> str:
    """Answer citing only the first excerpt, so `last_articles` is exactly the pinned Article."""
    return f"The provision applies [{excerpt_cites(request)[0]}]."


def chat_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "rate_limit_ip": "1000/minute",
        "rate_limit_session": "1000/minute",
        "log_format": "json",
        "log_level": "DEBUG",
        **overrides,
    }
    return Settings(_env_file=None, database_url=SecretStr(database_url), **values)  # type: ignore[call-arg]


def build_app(settings: Settings, provider: FakeProvider) -> FastAPI:
    async def factory(s: Settings) -> AppServices:
        session_factory = create_session_factory(create_engine(s))
        retrieval = RetrievalService(session_factory, EMBEDDER, FakeReranker(), s)
        return await fake_services(provider, retrieval, DbCallRecorder(session_factory))(s)

    return create_app(settings, factory)


def capture_logs(settings: Settings) -> io.StringIO:
    """Route every log line to a buffer (call after `create_app`, which configures stdout)."""
    stream = io.StringIO()
    configure_logging(settings, stream=stream)
    return stream


def log_events(stream: io.StringIO, event: str | None = None) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    return [line for line in lines if event is None or line["event"] == event]


@contextmanager
def chat_client(
    settings: Settings, provider: FakeProvider
) -> Iterator[tuple[TestClient, io.StringIO]]:
    """A started app and its captured logs. `llm_calls` rows are flushed on exit."""
    app = build_app(settings, provider)
    logs = capture_logs(settings)
    with TestClient(app) as client:
        yield client, logs


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[AppServices]:
    """Run the lifespan in the current event loop (for `httpx.ASGITransport`)."""
    async with app.router.lifespan_context(app):
        services: AppServices = app.state.services
        yield services


def query(database_url: str, sql: str, **params: Any) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return list(await conn.execute(text(sql), params))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def new_session(client: TestClient) -> str:
    response = client.post("/v1/sessions")
    assert response.status_code == 201
    return str(response.json()["session_id"])


def parse_sse(raw: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def stream_chat(
    client: TestClient, session_id: str, message: str, headers: dict[str, str] | None = None
) -> list[tuple[str, Any]]:
    body = {"session_id": session_id, "message": message}
    with client.stream("POST", "/v1/chat", json=body, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return parse_sse("".join(response.iter_text()))
