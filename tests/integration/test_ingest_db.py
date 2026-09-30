"""Ingestion storage against a real Postgres + pgvector (spec: ingestion §3.8, §7).

A small fixture corpus (preamble, Articles incl. an omitted and a split one, a Seventh Schedule
list, an Appendix) → chunks → FakeEmbedder → repository → rows with the right metadata.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from samvidhan.db.engine import create_session_factory
from samvidhan.db.models import EMBEDDING_DIM
from samvidhan.db.repositories.corpus import CorpusRepository, NewDocument
from samvidhan.ingestion.chunk import ChunkConfig, build_chunks
from samvidhan.ingestion.embed import FakeEmbedder
from samvidhan.ingestion.footnotes import Footnote
from samvidhan.ingestion.segment import Segment
from tests.unit.ingestion_helpers import INDENT, row

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ChunkConfig(max_tokens=80, target_min=20, target_max=60, schedule7_entries=2)


def words(value: str) -> int:
    return len(value.split())


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url


def _segments() -> list[Segment]:
    note = Footnote(42, 2, "Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002, s. 2.")
    part = {
        "part_no": "III",
        "part_title": "Fundamental Rights",
        "group_heading": "Right to Freedom",
    }
    long_clauses = [row(f"({n}) " + " ".join(["word"] * 15), x0=INDENT) for n in range(1, 6)]
    return [
        Segment("preamble", 0, (row("WE, THE PEOPLE OF INDIA"),)),
        Segment(
            "article",
            1,
            (row("No person shall be deprived."),),
            article_no="21",
            article_title="Protection of life and personal liberty",
            **part,
        ),  # type: ignore[arg-type]
        Segment(
            "article",
            2,
            (row("The State shall {{fn:2}}provide.", page=42),),
            footnotes=(note,),
            article_no="21A",
            article_title="Right to education",
            **part,
        ),  # type: ignore[arg-type]
        Segment(
            "article",
            3,
            (row("Omitted by the Constitution (Forty-fourth Amendment) Act, 1978."),),
            article_no="31",
            article_title="Compulsory acquisition of property",
            is_omitted=True,
            **part,
        ),  # type: ignore[arg-type]
        Segment(
            "article",
            4,
            tuple(long_clauses),
            article_no="368",
            part_no="XX",
            article_title="Power of Parliament to amend the Constitution",
        ),
        Segment(
            "schedule",
            5,
            (row("1. Public order.", x0=176.0), row("2. Police.", x0=176.0)),
            schedule_no="7",
            schedule_list="II",
            group_heading="List II—State List",
        ),
        Segment("appendix", 6, (row("C.O. 272"),), appendix_no="II", part_title="THE ORDER, 2019"),
    ]


def _rows() -> list[dict[str, Any]]:
    chunks = build_chunks(_segments(), words, CONFIG)
    vectors = FakeEmbedder(EMBEDDING_DIM).embed([c.embed_text for c in chunks])
    return [{**c.as_dict(), "embedding": v} for c, v in zip(chunks, vectors, strict=True)]


def _document(sha: str) -> NewDocument:
    return NewDocument("The Constitution of India", None, date(2024, 5, 1), sha, "v1", "fake")


def _run(database_url: str, work: Any) -> Any:
    from sqlalchemy.ext.asyncio import create_async_engine

    async def main() -> Any:
        engine = create_async_engine(database_url)
        try:
            async with create_session_factory(engine)() as session, session.begin():
                return await work(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


def test_insert_find_count_and_metadata(database_url: str) -> None:
    rows = _rows()

    async def insert(session: Any) -> uuid.UUID:
        return await CorpusRepository(session).insert_document_with_chunks(
            _document("a" * 64), rows
        )

    document_id = _run(database_url, insert)

    async def check(session: Any) -> dict[str, Any]:
        repo = CorpusRepository(session)
        found = await repo.find_document(sha256="a" * 64, chunker_version="v1", embed_model="fake")
        missing = await repo.find_document(
            sha256="b" * 64, chunker_version="v1", embed_model="fake"
        )
        result = await session.execute(
            text(
                "SELECT id, chunk_type, article_no, is_omitted, appendix_no, clause_range, "
                "jsonb_array_length(amendment_notes), vector_dims(embedding), tsv IS NOT NULL "
                "FROM chunks WHERE document_id = :d ORDER BY seq"
            ),
            {"d": document_id},
        )
        return {
            "found": found.id if found else None,
            "missing": missing,
            "count": await repo.count_chunks(document_id),
            "rows": {r[0]: r[1:] for r in result.all()},
        }

    got = _run(database_url, check)
    assert got["found"] == document_id and got["missing"] is None
    assert got["count"] == len(rows)
    by_id = got["rows"]
    assert by_id["art-21A#0"] == ("article", "21A", False, None, None, 1, EMBEDDING_DIM, True)
    assert by_id["art-31#0"][2] is True
    assert by_id["app-2#0"][:4] == ("appendix", None, False, "II")
    assert by_id["sch-7-list2#0"][4] == "entries 1-2"
    assert {k for k in by_id if k.startswith("art-368#")} == {"art-368#0", "art-368#1"}


def test_activate_leaves_exactly_one_active_document(database_url: str) -> None:
    async def insert_two(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        repo = CorpusRepository(session)
        first = await repo.insert_document_with_chunks(_document("c" * 64), [])
        second = await repo.insert_document_with_chunks(_document("d" * 64), [])
        return first, second

    first, second = _run(database_url, insert_two)

    def activate(target: uuid.UUID) -> list[uuid.UUID]:
        async def work(session: Any) -> list[uuid.UUID]:
            await CorpusRepository(session).activate(target)
            result = await session.execute(text("SELECT id FROM documents WHERE is_active"))
            return [r[0] for r in result.all()]

        return _run(database_url, work)  # type: ignore[no-any-return]

    assert activate(first) == [first]
    assert activate(second) == [second]

    async def missing(session: Any) -> None:
        await CorpusRepository(session).activate(uuid.uuid4())

    with pytest.raises(LookupError):
        _run(database_url, missing)


def test_same_key_cannot_be_inserted_twice(database_url: str) -> None:
    async def insert(session: Any) -> uuid.UUID:
        return await CorpusRepository(session).insert_document_with_chunks(_document("e" * 64), [])

    _run(database_url, insert)
    with pytest.raises(Exception, match="uq_documents"):
        _run(database_url, insert)


def test_migration_002_downgrade_and_upgrade(database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "001")

    async def columns(session: Any) -> list[str]:
        result = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'chunks'")
        )
        return [r[0] for r in result.all()]

    assert "appendix_no" not in _run(database_url, columns)
    command.upgrade(config, "head")
    assert "appendix_no" in _run(database_url, columns)
