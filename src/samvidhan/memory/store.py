"""Session memory store. Phase 4 keeps sessions in process (CLI chat, tests); Phase 5 adds the
Postgres implementation of the same protocol (HLD §9)."""

import uuid
from typing import Protocol

from samvidhan.memory.structured import apply_turn
from samvidhan.memory.types import SessionMemory, Turn


class MemoryStore(Protocol):
    async def load(self, session_id: uuid.UUID | None) -> SessionMemory: ...

    async def save_turn(self, session_id: uuid.UUID | None, turn: Turn) -> SessionMemory: ...


class InMemorySessionStore:
    """Dict-backed store; `None` session ids are never stored (one-shot questions)."""

    def __init__(self, max_messages: int) -> None:
        self._max_messages = max_messages
        self._sessions: dict[uuid.UUID, SessionMemory] = {}

    async def load(self, session_id: uuid.UUID | None) -> SessionMemory:
        if session_id is None:
            return SessionMemory()
        return self._sessions.get(session_id, SessionMemory())

    async def save_turn(self, session_id: uuid.UUID | None, turn: Turn) -> SessionMemory:
        memory = apply_turn(await self.load(session_id), turn, max_messages=self._max_messages)
        if session_id is not None:
            self._sessions[session_id] = memory
        return memory
