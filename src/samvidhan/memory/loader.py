"""Loads what the router and answer prompts see for a session (HLD §9.2, spec: api-sessions-memory
§3.3): rolling summary + structured memory + the last messages before the current question."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.db.repositories.chat import MessageRepository, SessionRepository
from samvidhan.memory.structured import from_json
from samvidhan.memory.types import ChatMessage, SessionMemory


async def load_session_memory(
    db: AsyncSession, session_id: uuid.UUID, *, before_message_id: int | None, history: int
) -> SessionMemory:
    """Unknown sessions load as empty memory (the API rejects them before the graph runs)."""
    row = await SessionRepository(db).get(session_id)
    if row is None:
        return SessionMemory()
    recent = (
        await MessageRepository(db).page(session_id, before=before_message_id, limit=history)
        if history > 0
        else []
    )
    messages = [ChatMessage("user" if m.role == "user" else "assistant", m.content) for m in recent]
    return from_json(row.memory or {}, summary=row.summary, messages=messages)
