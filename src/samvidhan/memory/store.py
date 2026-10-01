"""Session memory stores (HLD §9). `InMemorySessionStore` serves the CLI and tests;
`PostgresSessionStore` serves the API (spec: api-sessions-memory §3.3)."""

import dataclasses
import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from samvidhan.db.repositories.chat import (
    MessageRepository,
    NewAssistantMessage,
    SessionRepository,
)
from samvidhan.db.repositories.corpus import CorpusRepository
from samvidhan.memory.loader import load_session_memory
from samvidhan.memory.structured import apply_turn, from_json, to_json
from samvidhan.memory.types import SavedTurn, SessionMemory, Turn


class MemoryStore(Protocol):
    async def load(
        self, session_id: uuid.UUID | None, *, before_message_id: int | None = None
    ) -> SessionMemory: ...

    async def save_turn(self, session_id: uuid.UUID | None, turn: Turn) -> SavedTurn: ...


class InMemorySessionStore:
    """Dict-backed store; `None` session ids are never stored (one-shot questions)."""

    def __init__(self, max_messages: int) -> None:
        self._max_messages = max_messages
        self._sessions: dict[uuid.UUID, SessionMemory] = {}

    async def load(
        self, session_id: uuid.UUID | None, *, before_message_id: int | None = None
    ) -> SessionMemory:
        if session_id is None:
            return SessionMemory()
        return self._sessions.get(session_id, SessionMemory())

    async def save_turn(self, session_id: uuid.UUID | None, turn: Turn) -> SavedTurn:
        memory = apply_turn(await self.load(session_id), turn, max_messages=self._max_messages)
        if session_id is not None:
            self._sessions[session_id] = memory
        return SavedTurn(memory=memory, message_id=None)


class PostgresSessionStore:
    """Messages in `chat_messages`, structured memory in `chat_sessions.memory`."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, history_messages: int
    ) -> None:
        self._session_factory = session_factory
        self._history = history_messages

    async def load(
        self, session_id: uuid.UUID | None, *, before_message_id: int | None = None
    ) -> SessionMemory:
        if session_id is None:
            return SessionMemory()
        async with self._session_factory() as db:
            return await load_session_memory(
                db, session_id, before_message_id=before_message_id, history=self._history
            )

    async def save_turn(self, session_id: uuid.UUID | None, turn: Turn) -> SavedTurn:
        """Insert the assistant row and roll the structured memory forward in one transaction.
        The returned memory's `messages` holds only this turn (history is re-read on load)."""
        if session_id is None:
            return SavedTurn(apply_turn(SessionMemory(), turn, max_messages=2), None)
        async with self._session_factory() as db, db.begin():
            sessions = SessionRepository(db)
            row = await sessions.get_for_update(session_id)
            if row is None:  # deleted mid-request: nothing to attach the answer to
                return SavedTurn(apply_turn(SessionMemory(), turn, max_messages=2), None)
            articles = [ref for ref in turn.cited_refs if ref[:1].isdigit()]
            turn = dataclasses.replace(
                turn, cited_parts=await CorpusRepository(db).parts_for_articles(articles)
            )
            message = await MessageRepository(db).add_assistant(
                NewAssistantMessage(
                    session_id=session_id,
                    request_id=turn.request_id or "",
                    content=turn.assistant,
                    route=turn.route,
                    standalone_query=turn.standalone_query,
                    cited_articles=list(turn.cited_refs),
                    retrieval_trace=turn.retrieval_trace,
                    prompt_version=turn.prompt_version,
                    latency_ms=turn.latency_ms,
                )
            )
            previous = from_json(row.memory or {}, summary=row.summary, messages=[])
            memory = apply_turn(previous, turn, max_messages=2)
            await sessions.update_memory(session_id, to_json(memory))
        return SavedTurn(memory=memory, message_id=message.id)
