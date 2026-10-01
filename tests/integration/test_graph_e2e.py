"""The compiled graph end to end with FakeLLM over a real Postgres corpus (plan P4.10, spec §6).

Real retrieval (FakeEmbedder + FakeReranker), real `llm_calls` rows; only the model is fake.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from samvidhan.core.config import Settings
from samvidhan.db.engine import create_session_factory
from samvidhan.db.repositories.corpus import CorpusRepository
from samvidhan.generation import templates
from samvidhan.graph.builder import build_graph
from samvidhan.graph.nodes import GraphDeps, load_prompts
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.recorder import DbCallRecorder
from samvidhan.llm.types import ProviderRequest
from samvidhan.memory.store import InMemorySessionStore
from samvidhan.retrieval.rerank import FakeReranker
from samvidhan.retrieval.service import RetrievalService
from tests.integration.fixture_db import EDITION, EMBEDDER, migrate_and_seed

ROUTES: dict[str, dict[str, Any]] = {
    "What does Art. 21-A say?": {
        "type": "article_lookup", "standalone_query": "What does Article 21A say?",
        "article_refs": ["21-A"],
    },
    "Can police arrest me without telling me why?": {
        "type": "simple", "standalone_query": "arrested without being informed of the grounds",
    },
    "Compare Article 14 and Article 21": {
        "type": "multi_part", "standalone_query": "Compare Article 14 and Article 21",
        "article_refs": ["14", "21"],
        "sub_queries": ["equality before the law", "protection of life and personal liberty"],
    },
    "How does the Constitution protect the environment?": {
        "type": "conceptual", "standalone_query": "protect the environment forests wild life",
        "use_hyde": True,
    },
    "Punishment for theft under BNS?": {"type": "out_of_scope", "standalone_query": "BNS theft"},
    "thanks!": {"type": "chitchat", "standalone_query": "thanks!"},
    "What are my rights?": {
        "type": "ambiguous", "standalone_query": "rights",
        "clarification_question": "Which rights — for example on arrest, or equality?",
    },
}  # fmt: skip
_MESSAGE = re.compile(r"<message>\n(.*?)\n</message>", re.S)
_CITE = re.compile(r'cite="([^"]+)"')


def router(request: ProviderRequest) -> str:
    match = _MESSAGE.search(request.messages[-1]["content"])
    assert match, "router prompt must carry the message"
    return json.dumps(ROUTES[match.group(1)])


def answer(request: ProviderRequest) -> str:
    """Cites every excerpt it was given, plus one Article it wasn't (must be dropped)."""
    cites = _CITE.findall(request.messages[-1]["content"])
    return " ".join(f"Provision [{c}]." for c in cites) + " Also [Art. 999]."


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        migrate_and_seed(url)
        yield url


def _run(database_url: str, settings: Settings, messages: list[str], session: bool = False) -> Any:
    async def main() -> tuple[list[dict[str, Any]], FakeProvider, list[Any]]:
        engine = create_async_engine(database_url)
        try:
            factory = create_session_factory(engine)
            async with factory() as db:
                edition = await CorpusRepository(db).active_version_date()
            provider = FakeProvider(
                {"router": router, "answer": answer, "hyde": "The State shall protect forests."}
            )
            recorder = DbCallRecorder(factory)
            graph = build_graph(
                GraphDeps(
                    llm=fake_llm(provider, recorder),
                    retrieval=RetrievalService(factory, EMBEDDER, FakeReranker(), settings),
                    memory=InMemorySessionStore(max_messages=settings.raw_window),
                    settings=settings,
                    prompts=load_prompts(settings),
                    edition=edition,
                )
            )
            session_id = uuid.uuid4() if session else None
            finals = []
            request_ids = []
            for message in messages:
                request_id = uuid.uuid4().hex
                request_ids.append(request_id)
                structlog.contextvars.bind_contextvars(request_id=request_id)
                finals.append(
                    await graph.ainvoke(
                        {"request_id": request_id, "session_id": session_id, "message": message}
                    )
                )
            await recorder.drain()
            async with engine.connect() as conn:
                rows = list(
                    await conn.execute(
                        text(
                            "SELECT request_id, purpose, status FROM llm_calls "
                            "WHERE request_id = ANY(:ids) ORDER BY id"
                        ),
                        {"ids": request_ids},
                    )
                )
            return finals, provider, rows
        finally:
            await engine.dispose()

    return asyncio.run(main())


def test_every_route_type_end_to_end(database_url: str, settings: Settings) -> None:
    finals, provider, rows = _run(database_url, settings, list(ROUTES))
    by_message = {f["message"]: f for f in finals}

    lookup = by_message["What does Art. 21-A say?"]
    assert lookup["route"].type == "article_lookup"
    assert lookup["retrieval"].refs == ["21A"] and lookup["context"][0].id == "art-21A#0"
    assert lookup["citations"][0].label == "Art. 21A"
    assert lookup["citations"][0].title == "Right to education"
    assert lookup["invalid_citations"] == ["999"] and "[Art. 999]" not in lookup["answer"]
    assert "(as on 1 May 2024)" in lookup["answer"] and EDITION.year == 2024

    simple = by_message["Can police arrest me without telling me why?"]
    assert simple["context"][0].id == "art-22#0" and simple["citations"][0].ref == "22"

    multi = by_message["Compare Article 14 and Article 21"]
    assert multi["retrieval"].trace["mode"] == "decompose"
    assert {"14", "21"} <= {c.ref for c in multi["citations"]}

    conceptual = by_message["How does the Constitution protect the environment?"]
    assert conceptual["hyde_passage"] == "The State shall protect forests."
    assert conceptual["retrieval"].trace["dense_query"] == "hyde"
    assert "48A" in {c.ref for c in conceptual["citations"]}

    assert by_message["Punishment for theft under BNS?"]["answer"] == templates.OUT_OF_SCOPE
    assert by_message["thanks!"]["answer"].startswith("You're welcome")
    assert by_message["What are my rights?"]["answer"].startswith("Which rights")

    # Every provider call left exactly one llm_calls row, with the request's id.
    assert len(rows) == len(provider.calls)
    purposes = [r.purpose for r in rows]
    assert purposes.count("router") == len(ROUTES)
    assert purposes.count("answer") == 4 and purposes.count("hyde") == 1
    assert {r.status for r in rows} == {"ok"}


def test_follow_up_in_a_session_resolves_from_memory(database_url: str, settings: Settings) -> None:
    finals, provider, _ = _run(
        database_url,
        settings,
        ["What does Art. 21-A say?", "Can police arrest me without telling me why?"],
        session=True,
    )
    second_router_prompt = provider.calls[2].messages[-1]["content"]
    assert "last_articles: 21A" in second_router_prompt  # the fake cites every excerpt
    memory = finals[1]["memory"]
    assert memory.last_articles[0] == "22" and {"21A", "22"} <= set(memory.articles_discussed)
    assert len(finals[1]["memory"].messages) == 4
