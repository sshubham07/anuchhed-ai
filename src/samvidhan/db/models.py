"""ORM models (HLD §10). Schema changes go through Alembic migrations, never `create_all`."""

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024  # bge-m3 dense output size (ADR-0008); changing it needs a migration.

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("sha256", "chunker_version", "embed_model"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    version_date: Mapped[date] = mapped_column(Date)
    sha256: Mapped[str] = mapped_column(Text)
    chunker_version: Mapped[str] = mapped_column(Text)
    embed_model: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("chunk_type IN ('preamble','article','schedule')", name="chunk_type"),
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("chunks_tsv_gin", "tsv", postgresql_using="gin"),
        Index("chunks_article", "document_id", "article_no"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'art-21#0', 'sch-7-list2#3'
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_type: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer)
    part_no: Mapped[str | None] = mapped_column(Text)
    part_title: Mapped[str | None] = mapped_column(Text)
    chapter: Mapped[str | None] = mapped_column(Text)
    group_heading: Mapped[str | None] = mapped_column(Text)
    article_no: Mapped[str | None] = mapped_column(Text)
    article_title: Mapped[str | None] = mapped_column(Text)
    schedule_no: Mapped[str | None] = mapped_column(Text)
    clause_range: Mapped[str | None] = mapped_column(Text)
    is_omitted: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    amendment_notes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb")
    )
    text: Mapped[str] = mapped_column(Text)
    embed_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', embed_text)", persisted=True)
    )
