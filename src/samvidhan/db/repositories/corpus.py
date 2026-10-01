"""Corpus repository: documents + chunks (spec: ingestion §3.8, HLD §10).

A document is keyed by (sha256, chunker_version, embed_model); it is inserted together with all its
chunks in one transaction, inactive. `activate` makes it the single active document.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.db.models import Chunk, Document


@dataclass(frozen=True, slots=True)
class NewDocument:
    title: str
    source_url: str | None
    version_date: date
    sha256: str
    chunker_version: str
    embed_model: str


class CorpusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_document(
        self, *, sha256: str, chunker_version: str, embed_model: str
    ) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.sha256 == sha256,
                Document.chunker_version == chunker_version,
                Document.embed_model == embed_model,
            )
        )
        return result.scalar_one_or_none()

    async def insert_document_with_chunks(
        self, document: NewDocument, chunks: Sequence[dict[str, Any]]
    ) -> uuid.UUID:
        """Insert the document (inactive) and its chunk rows; the caller commits."""
        document_id = uuid.uuid4()
        self._session.add(
            Document(
                id=document_id,
                title=document.title,
                source_url=document.source_url,
                version_date=document.version_date,
                sha256=document.sha256,
                chunker_version=document.chunker_version,
                embed_model=document.embed_model,
                is_active=False,
            )
        )
        await self._session.flush()
        if chunks:
            await self._session.execute(
                insert(Chunk), [{**row, "document_id": document_id} for row in chunks]
            )
        return document_id

    async def activate(self, document_id: uuid.UUID) -> None:
        """Make `document_id` the only active document (blue/green switch); the caller commits."""
        await self._session.execute(update(Document).values(is_active=False))
        result = await self._session.execute(
            update(Document).where(Document.id == document_id).values(is_active=True)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise LookupError(f"Document {document_id} not found")

    async def count_chunks(self, document_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        )
        return int(result.scalar_one())

    async def active_version_date(self) -> date | None:
        """Edition ("as on") date of the active document, shown in the answer disclaimer."""
        result = await self._session.execute(
            select(Document.version_date).where(Document.is_active)
        )
        return result.scalar_one_or_none()
