"""A migrated Postgres + pgvector with a tiny active corpus (FakeEmbedder vectors)."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from samvidhan.db.engine import create_session_factory
from samvidhan.db.models import EMBEDDING_DIM
from samvidhan.db.repositories.corpus import CorpusRepository, NewDocument
from samvidhan.ingestion.embed import FakeEmbedder

ROOT = Path(__file__).resolve().parents[2]
EMBEDDER = FakeEmbedder(EMBEDDING_DIM)
EDITION = date(2024, 5, 1)

# (id, chunk_type, article_no, schedule_no, title, embed_text)
CORPUS = [
    ("preamble#0", "preamble", None, None, None,
     "Preamble\n\nWE, THE PEOPLE OF INDIA, having solemnly resolved to constitute India into a "
     "SOVEREIGN SOCIALIST SECULAR DEMOCRATIC REPUBLIC"),
    ("art-14#0", "article", "14", None, "Equality before law",
     "Article 14: Equality before law. The State shall not deny to any person equality before "
     "the law or the equal protection of the laws"),
    ("art-21#0", "article", "21", None, "Protection of life and personal liberty",
     "Article 21: Protection of life and personal liberty. No person shall be deprived of his "
     "life or personal liberty except according to procedure established by law"),
    ("art-21A#0", "article", "21A", None, "Right to education",
     "Article 21A: Right to education. The State shall provide free and compulsory education to "
     "all children of the age of six to fourteen years"),
    ("art-22#0", "article", "22", None, "Protection against arrest and detention in certain cases",
     "Article 22: Protection against arrest and detention. No person who is arrested shall be "
     "detained in custody without being informed of the grounds for such arrest"),
    ("art-48A#0", "article", "48A", None, "Protection and improvement of environment",
     "Article 48A: The State shall endeavour to protect and improve the environment and to "
     "safeguard the forests and wild life of the country"),
    ("sch-7-list2#0", "schedule", None, "7", None,
     "Seventh Schedule List II State List: Police. Public order"),
]  # fmt: skip


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _rows() -> list[dict[str, Any]]:
    vectors = EMBEDDER.embed([text for *_, text in CORPUS])
    return [
        {
            "id": chunk_id,
            "chunk_type": chunk_type,
            "seq": seq,
            "article_no": article_no,
            "schedule_no": schedule_no,
            "article_title": title,
            "text": text,
            "embed_text": text,
            "token_count": len(text.split()),
            "embedding": vector,
        }
        for seq, (
            (chunk_id, chunk_type, article_no, schedule_no, title, text),
            vector,
        ) in enumerate(zip(CORPUS, vectors, strict=True))
    ]


def migrate_and_seed(url: str) -> None:
    command.upgrade(alembic_config(url), "head")

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with create_session_factory(engine)() as session, session.begin():
                repo = CorpusRepository(session)
                doc = NewDocument("Constitution", None, EDITION, "c" * 64, "v1", "fake")
                await repo.activate(await repo.insert_document_with_chunks(doc, _rows()))
        finally:
            await engine.dispose()

    asyncio.run(seed())
