"""Corpus tables: documents, chunks (HLD §10)

Revision ID: 001
Revises:
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("version_date", sa.Date(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("chunker_version", sa.Text(), nullable=False),
        sa.Column("embed_model", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("sha256", "chunker_version", "embed_model", name="uq_documents_sha256"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_type", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("part_no", sa.Text(), nullable=True),
        sa.Column("part_title", sa.Text(), nullable=True),
        sa.Column("chapter", sa.Text(), nullable=True),
        sa.Column("group_heading", sa.Text(), nullable=True),
        sa.Column("article_no", sa.Text(), nullable=True),
        sa.Column("article_title", sa.Text(), nullable=True),
        sa.Column("schedule_no", sa.Text(), nullable=True),
        sa.Column("clause_range", sa.Text(), nullable=True),
        sa.Column("is_omitted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "amendment_notes",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embed_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', embed_text)", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_type IN ('preamble','article','schedule')", name="ck_chunks_chunk_type"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "id", name="pk_chunks"),
    )
    op.create_index(
        "chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("chunks_article", "chunks", ["document_id", "article_no"])


def downgrade() -> None:
    # The `vector` extension is intentionally left installed (spec: foundation §3.3).
    op.drop_table("chunks")
    op.drop_table("documents")
