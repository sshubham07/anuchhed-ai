"""Retrieval against a real Postgres + pgvector (spec: retrieval §6–7).

A tiny active corpus embedded with FakeEmbedder, plus an inactive copy that must never be returned.
"""

import asyncio
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from samvidhan.core.config import Settings
from samvidhan.db.engine import create_session_factory
from samvidhan.db.models import EMBEDDING_DIM
from samvidhan.db.repositories.corpus import CorpusRepository, NewDocument
from samvidhan.ingestion.embed import FakeEmbedder
from samvidhan.retrieval.dense import dense_search
from samvidhan.retrieval.lexical import lexical_search
from samvidhan.retrieval.lookup import fetch_pinned, load_known_refs
from samvidhan.retrieval.rerank import FakeReranker
from samvidhan.retrieval.service import RetrievalService

ROOT = Path(__file__).resolve().parents[2]
EMBEDDER = FakeEmbedder(EMBEDDING_DIM)

# (id, chunk_type, article_no, schedule_no, embed_text)
CORPUS = [
    (
        "preamble#0",
        "preamble",
        None,
        None,
        "Preamble\n\nWE, THE PEOPLE OF INDIA, sovereign republic",
    ),
    (
        "art-14#0",
        "article",
        "14",
        None,
        "Article 14: Equality before law. The State shall not deny",
    ),
    ("art-21#0", "article", "21", None, "Article 21: Protection of life and personal liberty"),
    ("art-21A#0", "article", "21A", None, "Article 21A: Right to education. Free and compulsory"),
    (
        "art-21A#1",
        "article",
        "21A",
        None,
        "Article 21A: children of the age of six to fourteen years",
    ),
    ("art-22#0", "article", "22", None, "Article 22: Protection against arrest and detention"),
    (
        "sch-7-list2#0",
        "schedule",
        None,
        "7",
        "Seventh Schedule List II State List: Police. Public order",
    ),
]


def _rows(prefix: str = "") -> list[dict[str, Any]]:
    vectors = EMBEDDER.embed([text for *_, text in CORPUS])
    return [
        {
            "id": chunk_id,
            "chunk_type": chunk_type,
            "seq": seq,
            "article_no": article_no,
            "schedule_no": schedule_no,
            "text": prefix + text,
            "embed_text": prefix + text,
            "token_count": len(text.split()),
            "embedding": vector,
        }
        for seq, ((chunk_id, chunk_type, article_no, schedule_no, text), vector) in enumerate(
            zip(CORPUS, vectors, strict=True)
        )
    ]


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")

        async def seed() -> None:
            engine = create_async_engine(url)
            async with create_session_factory(engine)() as session, session.begin():
                repo = CorpusRepository(session)
                doc = NewDocument("Constitution", None, date(2024, 5, 1), "a" * 64, "v1", "fake")
                active = await repo.insert_document_with_chunks(doc, _rows())
                old = NewDocument("Constitution", None, date(2020, 1, 1), "b" * 64, "v1", "fake")
                await repo.insert_document_with_chunks(old, _rows(prefix="OLD "))
                await repo.activate(active)
            await engine.dispose()

        asyncio.run(seed())
        yield url


def _with_session(database_url: str, work: Any) -> Any:
    async def main() -> Any:
        engine = create_async_engine(database_url)
        try:
            async with create_session_factory(engine)() as session:
                return await work(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


def test_dense_ranks_exact_text_first_and_ignores_inactive(database_url: str) -> None:
    query = EMBEDDER.embed([CORPUS[2][4]])[0]

    async def work(session: AsyncSession) -> Any:
        return await dense_search(session, query, k=10)

    hits = _with_session(database_url, work)
    assert hits[0].id == "art-21#0" and hits[0].ranks["dense"] == 1
    assert hits[0].score == pytest.approx(1.0, abs=1e-4)
    assert len(hits) == len(CORPUS)
    assert not any(h.text.startswith("OLD") for h in hits)


def test_lexical_uses_or_semantics(database_url: str) -> None:
    async def work(session: AsyncSession) -> Any:
        return (
            await lexical_search(session, "who controls police and arrest", k=10),
            await lexical_search(session, "who controls police and arrest", k=10, match_all=True),
            await lexical_search(session, "the of and", k=10),
        )

    any_term, all_terms, stop_words = _with_session(database_url, work)
    assert {h.id for h in any_term} == {"sch-7-list2#0", "art-22#0"}
    assert all_terms == []
    assert stop_words == []


def test_lookup_fetches_every_chunk_in_order(database_url: str) -> None:
    async def work(session: AsyncSession) -> Any:
        return (
            await fetch_pinned(session, ["21A", "SCH-7", "PREAMBLE"]),
            await load_known_refs(session),
        )

    pinned, known = _with_session(database_url, work)
    assert [c.id for c in pinned] == ["preamble#0", "art-21A#0", "art-21A#1", "sch-7-list2#0"]
    assert all(c.pinned for c in pinned)
    assert known == {"PREAMBLE", "14", "21", "21A", "22", "SCH-7"}


def test_service_end_to_end(database_url: str, settings: Settings) -> None:
    async def main() -> Any:
        engine = create_async_engine(database_url)
        try:
            factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
            svc = RetrievalService(factory, EMBEDDER, FakeReranker(), settings)
            return await svc.retrieve("protection against arrest and detention", refs=["Art. 21-A"])
        finally:
            await engine.dispose()

    result = asyncio.run(main())
    ids = [c.id for c in result.chunks]
    assert ids[:2] == ["art-21A#0", "art-21A#1"]
    assert ids[2] == "art-22#0"
    assert result.refs == ["21A"] and not result.low_confidence
    assert result.trace["pinned"] == ["art-21A#0", "art-21A#1"]
