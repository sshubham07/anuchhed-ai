"""Chat repositories: sessions, messages, feedback (HLD §9–10, spec: api-sessions-memory §3.2).

Each takes an `AsyncSession`; the caller commits.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from samvidhan.db.models import ChatMessage, ChatSession, Feedback


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, session_id: uuid.UUID, client_ip_hash: str | None) -> ChatSession:
        row = ChatSession(id=session_id, client_ip_hash=client_ip_hash, memory={})
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, session_id: uuid.UUID) -> ChatSession | None:
        return await self._session.get(ChatSession, session_id)

    async def get_for_update(self, session_id: uuid.UUID) -> ChatSession | None:
        """Row-locked read: concurrent turns of one session can't overwrite each other's memory.
        FOR NO KEY UPDATE, so a parallel request can still insert its user message (FK check)."""
        result = await self._session.execute(
            select(ChatSession).where(ChatSession.id == session_id).with_for_update(key_share=True)
        )
        return result.scalar_one_or_none()

    async def delete(self, session_id: uuid.UUID) -> bool:
        """Delete the session; messages cascade, feedback keeps its snapshot. False if unknown."""
        result = await self._session.execute(
            delete(ChatSession).where(ChatSession.id == session_id).returning(ChatSession.id)
        )
        return result.scalar_one_or_none() is not None

    async def count_created_since(self, client_ip_hash: str, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(ChatSession)
            .where(ChatSession.client_ip_hash == client_ip_hash, ChatSession.created_at >= since)
        )
        return int(result.scalar_one())

    async def update_memory(self, session_id: uuid.UUID, memory: dict[str, Any]) -> None:
        await self._session.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(memory=memory, last_active_at=func.now())
        )

    async def count_messages(self, session_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == session_id)
        )
        return int(result.scalar_one())


@dataclass(frozen=True, slots=True)
class NewAssistantMessage:
    session_id: uuid.UUID
    request_id: str
    content: str
    route: dict[str, Any] | None
    standalone_query: str | None
    cited_articles: list[str]
    retrieval_trace: dict[str, Any] | None
    prompt_version: str | None
    latency_ms: dict[str, int]


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_user(self, session_id: uuid.UUID, request_id: str, content: str) -> ChatMessage:
        row = ChatMessage(
            session_id=session_id, request_id=request_id, role="user", content=content
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def add_assistant(self, message: NewAssistantMessage) -> ChatMessage:
        row = ChatMessage(
            session_id=message.session_id,
            request_id=message.request_id,
            role="assistant",
            content=message.content,
            route=message.route,
            standalone_query=message.standalone_query,
            cited_articles=message.cited_articles,
            retrieval_trace=message.retrieval_trace,
            prompt_version=message.prompt_version,
            latency_ms=message.latency_ms,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, message_id: int) -> ChatMessage | None:
        return await self._session.get(ChatMessage, message_id)

    async def page(
        self, session_id: uuid.UUID, *, before: int | None, limit: int
    ) -> list[ChatMessage]:
        """Up to `limit` messages with `id < before` (all when `before` is None), oldest first."""
        query = select(ChatMessage).where(ChatMessage.session_id == session_id)
        if before is not None:
            query = query.where(ChatMessage.id < before)
        result = await self._session.execute(query.order_by(ChatMessage.id.desc()).limit(limit))
        return list(reversed(result.scalars().all()))

    async def previous_user(self, message: ChatMessage) -> ChatMessage | None:
        """The user message that `message` (an assistant row) answered."""
        result = await self._session.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == message.session_id,
                ChatMessage.id < message.id,
                ChatMessage.role == "user",
            )
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, message: ChatMessage, question: str | None, rating: int, comment: str | None
    ) -> Feedback:
        """Store the rating with a question/answer snapshot that survives session expiry."""
        row = Feedback(
            message_id=message.id,
            rating=rating,
            comment=comment,
            question=question,
            answer=message.content,
        )
        self._session.add(row)
        await self._session.flush()
        return row


@dataclass(frozen=True, slots=True)
class ExpiryCounts:
    sessions: int
    messages: int
    feedback: int  # feedback rows unlinked from the expired messages (snapshot kept)


class ExpiryRepository:
    """Session expiry (HLD §9.1, spec: api-sessions-memory §9.1): sessions idle since before
    `cutoff` are deleted with their messages; their feedback keeps only its snapshot."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _expired(cutoff: datetime) -> Any:
        return ChatSession.last_active_at < cutoff

    async def count(self, cutoff: datetime) -> ExpiryCounts:
        """What a run would expire now (`--dry-run`)."""
        n_sessions = await self._count(
            select(func.count()).select_from(ChatSession).where(self._expired(cutoff))
        )
        ids = select(ChatSession.id).where(self._expired(cutoff)).scalar_subquery()
        return await self._counts(ids, n_sessions)

    async def expire_batch(self, cutoff: datetime, limit: int) -> ExpiryCounts:
        """Anonymize feedback and delete up to `limit` expired sessions. Rows locked by a live
        request are skipped (the next run gets them)."""
        result = await self._session.execute(
            select(ChatSession.id)
            .where(self._expired(cutoff))
            .order_by(ChatSession.last_active_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        ids = list(result.scalars().all())
        if not ids:
            return ExpiryCounts(0, 0, 0)
        counts = await self._counts(ids, len(ids))
        await self._snapshot_feedback(ids)
        await self._session.execute(delete(ChatSession).where(ChatSession.id.in_(ids)))
        return counts

    async def _snapshot_feedback(self, session_ids: list[uuid.UUID]) -> None:
        """Guard: fill a missing question/answer snapshot before the message rows go
        (`FeedbackRepository.add` normally fills both)."""
        user = aliased(ChatMessage)
        question = (
            select(user.content)
            .where(
                user.session_id == ChatMessage.session_id,
                user.id < ChatMessage.id,
                user.role == "user",
            )
            .order_by(user.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        await self._session.execute(
            update(Feedback)
            .where(
                Feedback.message_id == ChatMessage.id,
                ChatMessage.session_id.in_(session_ids),
                or_(Feedback.question.is_(None), Feedback.answer.is_(None)),
            )
            .values(
                question=func.coalesce(Feedback.question, question),
                answer=func.coalesce(Feedback.answer, ChatMessage.content),
            )
        )

    async def _counts(self, session_ids: Any, n_sessions: int) -> ExpiryCounts:
        in_sessions = ChatMessage.session_id.in_(session_ids)
        messages = await self._count(
            select(func.count()).select_from(ChatMessage).where(in_sessions)
        )
        feedback = await self._count(
            select(func.count())
            .select_from(Feedback)
            .join(ChatMessage, Feedback.message_id == ChatMessage.id)
            .where(in_sessions)
        )
        return ExpiryCounts(n_sessions, messages, feedback)

    async def _count(self, query: Select[int]) -> int:
        return int((await self._session.execute(query)).scalar_one())
