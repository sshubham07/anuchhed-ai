"""Conversation memory types (HLD §9.2–9.3)."""

from dataclasses import dataclass, field
from typing import Literal

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
    recent_topics: list[str] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)  # oldest first


@dataclass(frozen=True, slots=True)
class Turn:
    """One finished exchange, saved after the answer."""

    user: str
    assistant: str
    standalone_query: str
    cited_refs: list[str]
    route_type: str
