"""Appendix chunks: `appendix` chunk type + `appendix_no` (spec: ingestion §3.8)

Revision ID: 002
Revises: 001
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Migration 001 passed an already-prefixed name through the naming convention, so the live
# constraint is double-prefixed. 002 replaces it with the intended name.
_OLD_CHECK = "ck_chunks_ck_chunks_chunk_type"
_NEW_CHECK = "ck_chunks_chunk_type"


def upgrade() -> None:
    op.add_column("chunks", sa.Column("appendix_no", sa.Text(), nullable=True))
    # IF EXISTS on both names: a DB built another way may already use the intended name.
    op.execute(f"ALTER TABLE chunks DROP CONSTRAINT IF EXISTS {_OLD_CHECK}")
    op.execute(f"ALTER TABLE chunks DROP CONSTRAINT IF EXISTS {_NEW_CHECK}")
    op.execute(
        f"ALTER TABLE chunks ADD CONSTRAINT {_NEW_CHECK} "
        "CHECK (chunk_type IN ('preamble','article','schedule','appendix'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM chunks WHERE chunk_type = 'appendix'")
    op.execute(f"ALTER TABLE chunks DROP CONSTRAINT {_NEW_CHECK}")
    op.execute(
        f"ALTER TABLE chunks ADD CONSTRAINT {_OLD_CHECK} "
        "CHECK (chunk_type IN ('preamble','article','schedule'))"
    )
    op.drop_column("chunks", "appendix_no")
