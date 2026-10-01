"""Conversation memory types (HLD §9.2–9.3)."""

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class SessionMemory:
    """What the router and answer prompts see: summary + structured memory + recent messages."""

    summary: str | None = None
    last_articles: list[str] = field(default_factory=list)
    articles_discussed: list[str] = field(default_factory=list)
    parts_discussed: list[str] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)  # oldest first


@dataclass(frozen=True, slots=True)
class Turn:
    """One finished exchange, saved after the answer. The fields after `route_type` are only
    persisted by the Postgres store (the assistant `chat_messages` row)."""

    user: str
    assistant: str
    standalone_query: str
    cited_refs: list[str]
    route_type: str
    cited_parts: list[str] = field(default_factory=list)  # filled by the store from cited refs
    request_id: str | None = None
    route: dict[str, Any] | None = None
    retrieval_trace: dict[str, Any] | None = None
    prompt_version: str | None = None
    latency_ms: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SavedTurn:
    memory: SessionMemory
    message_id: int | None  # assistant `chat_messages.id`; None when nothing was persisted
