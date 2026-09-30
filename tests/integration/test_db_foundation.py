"""Migration 001 up/down and /readyz against a real Postgres + pgvector (testcontainers)."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from samvidhan.api.main import create_app
from samvidhan.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


def _alembic(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _query(database_url: str, sql: str) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return [row[0] for row in await conn.execute(text(sql))]
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_migration_001_upgrade_and_downgrade(database_url: str) -> None:
    config = _alembic(database_url)
    command.upgrade(config, "head")

    tables = _query(database_url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    assert {"documents", "chunks", "alembic_version"} <= set(tables)
    assert _query(database_url, "SELECT extname FROM pg_extension") == ["plpgsql", "vector"]
    indexes = _query(database_url, "SELECT indexdef FROM pg_indexes WHERE tablename = 'chunks'")
    assert any("hnsw (embedding vector_cosine_ops)" in ix for ix in indexes)
    assert any("gin (tsv)" in ix for ix in indexes)
    assert any("(document_id, article_no)" in ix for ix in indexes)

    command.downgrade(config, "base")
    tables = _query(database_url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    assert "chunks" not in tables
    assert "documents" not in tables

    command.upgrade(config, "head")  # re-applies cleanly after a downgrade


def test_readyz_against_real_database(database_url: str, settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"database_url": SecretStr(database_url)}))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok"}


def test_readyz_when_database_unreachable(settings: Settings) -> None:
    unreachable = "postgresql+asyncpg://test:test@127.0.0.1:1/samvidhan"
    app = create_app(settings.model_copy(update={"database_url": SecretStr(unreachable)}))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
