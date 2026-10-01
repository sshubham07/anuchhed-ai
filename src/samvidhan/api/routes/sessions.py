"""Anonymous sessions and their history (HLD §9.1, §11; spec: api-sessions-memory §3.4)."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.api.deps import get_app_settings, get_client_ip_hash, get_db
from samvidhan.api.schemas import ErrorEnvelope, MessageOut, MessagesPage, SessionCreated
from samvidhan.core.config import Settings
from samvidhan.core.errors import NotFoundError, RateLimitedError
from samvidhan.core.ids import new_session_id
from samvidhan.core.logging import get_logger
from samvidhan.db.repositories.chat import MessageRepository, SessionRepository

log = get_logger(__name__)

router = APIRouter(tags=["sessions"])

Db = Annotated[AsyncSession, Depends(get_db)]
SESSION_NOT_FOUND = "Session not found"


def _seconds_to_utc_midnight(now: datetime) -> int:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, 86400 - int((now - midnight).total_seconds()))


@router.post("/sessions", status_code=201, responses={429: {"model": ErrorEnvelope}})
async def create_session(
    db: Db,
    settings: Annotated[Settings, Depends(get_app_settings)],
    ip_hash: Annotated[str, Depends(get_client_ip_hash)],
) -> SessionCreated:
    """New anonymous session. Counted per IP per UTC day in the table (survives restarts)."""
    sessions = SessionRepository(db)
    # Check-then-insert: parallel requests may overshoot by one or two; acceptable for a soft cap.
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if await sessions.count_created_since(ip_hash, today) >= settings.sessions_per_ip_daily:
        log.info("rate_limited", scope="sessions_per_ip")
        raise RateLimitedError(
            _seconds_to_utc_midnight(now), "Too many new chats today. Please try again tomorrow."
        )
    row = await sessions.create(new_session_id(), ip_hash)
    await db.commit()
    return SessionCreated(session_id=row.id)


@router.get("/sessions/{session_id}/messages", responses={404: {"model": ErrorEnvelope}})
async def list_messages(
    session_id: uuid.UUID,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> MessagesPage:
    """A page of history, oldest first; `next_before` fetches the page before it."""
    if await SessionRepository(db).get(session_id) is None:
        raise NotFoundError(SESSION_NOT_FOUND)
    rows = await MessageRepository(db).page(session_id, before=before, limit=limit + 1)
    has_more = len(rows) > limit
    rows = rows[-limit:]
    return MessagesPage(
        messages=[
            MessageOut(
                id=r.id,
                role="user" if r.role == "user" else "assistant",
                content=r.content,
                cited_articles=list(r.cited_articles or []),
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_before=rows[0].id if has_more and rows else None,
    )


@router.delete("/sessions/{session_id}", status_code=204, responses={404: {"model": ErrorEnvelope}})
async def delete_session(session_id: uuid.UUID, db: Db) -> Response:
    """The user clears the chat: messages go with it; feedback keeps its snapshot."""
    if not await SessionRepository(db).delete(session_id):
        raise NotFoundError(SESSION_NOT_FOUND)
    await db.commit()
    return Response(status_code=204)
