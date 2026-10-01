"""Thumbs up/down on an answer (HLD §11; spec: api-sessions-memory §3.4)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.api.deps import get_client_ip_hash, get_db, get_services
from samvidhan.api.schemas import ErrorEnvelope, FeedbackCreated, FeedbackIn
from samvidhan.api.services import AppServices
from samvidhan.core.errors import NotFoundError
from samvidhan.core.logging import get_logger
from samvidhan.db.repositories.chat import FeedbackRepository, MessageRepository

log = get_logger(__name__)

router = APIRouter(tags=["feedback"])


@router.post(
    "/messages/{message_id}/feedback",
    status_code=201,
    responses={404: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}},
)
async def add_feedback(
    message_id: int,
    body: FeedbackIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    services: Annotated[AppServices, Depends(get_services)],
    ip_hash: Annotated[str, Depends(get_client_ip_hash)],
) -> FeedbackCreated:
    await services.limiter.check_ip(ip_hash)  # ids are sequential: cap probing and comment spam
    messages = MessageRepository(db)
    message = await messages.get(message_id)
    if message is None or message.role != "assistant":
        raise NotFoundError("Answer not found")
    question = await messages.previous_user(message)
    row = await FeedbackRepository(db).add(
        message, question.content if question else None, body.rating, body.comment
    )
    await db.commit()
    log.info("feedback_received", message_id=message_id, rating=body.rating)
    return FeedbackCreated(feedback_id=row.id)
