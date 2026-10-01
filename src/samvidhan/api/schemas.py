"""Request/response models (standards §3). Never return ORM objects."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    checks: dict[str, Literal["ok"]]


# ---- Sessions (HLD §11) ----


class SessionCreated(BaseModel):
    session_id: uuid.UUID


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    cited_articles: list[str]
    created_at: datetime


class MessagesPage(BaseModel):
    messages: list[MessageOut]  # oldest first
    next_before: int | None  # pass as `before` for the previous page; None when there is none


# ---- Chat ----


class ChatRequest(BaseModel):
    session_id: uuid.UUID
    # Length and emptiness are checked in the route so they map to MESSAGE_TOO_LONG / EMPTY_MESSAGE.
    message: str
    stream: bool = True
    # UI style toggle; None (Auto) keeps the router's choice (spec: api-sessions-memory §10).
    answer_style: Literal["brief", "detailed", "exam"] | None = None


class CitationOut(BaseModel):
    ref: str
    label: str
    title: str | None


class RouteOut(BaseModel):
    type: str
    standalone_query: str
    refs: list[str]
    answer_style: str


class ChatResponse(BaseModel):
    message_id: int | None
    answer: str
    citations: list[CitationOut]
    route: RouteOut | None
    low_confidence: bool
    latency_ms: dict[str, int]


# ---- Feedback ----


class FeedbackIn(BaseModel):
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackCreated(BaseModel):
    feedback_id: int


# ---- Corpus ----


class ArticleOut(BaseModel):
    ref: str  # canonical: 21A, SCH-7, APP-I, PREAMBLE
    label: str  # Art. 21A
    title: str | None
    part_no: str | None
    part_title: str | None
    is_omitted: bool
    text: str
    chunk_ids: list[str]


class MetaOut(BaseModel):
    app_version: str
    edition_date: date | None
    chunker_version: str
    embed_model: str
    rerank_model: str
    router_model: str
    answer_model: str
    prompt_versions: dict[str, str]
