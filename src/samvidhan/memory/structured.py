"""Structured memory update, no LLM (HLD §9.3): free, instant, can't hallucinate."""

from collections.abc import Sequence

from samvidhan.memory.types import ChatMessage, SessionMemory, Turn

ARTICLES_DISCUSSED_MAX = 15
RECENT_TOPICS_MAX = 5


def _latest_unique(items: Sequence[str], limit: int) -> list[str]:
    """Keep the last `limit` distinct items, most recent last."""
    out: list[str] = []
    for item in reversed(items):
        if item not in out:
            out.append(item)
        if len(out) == limit:
            break
    return list(reversed(out))


def apply_turn(memory: SessionMemory, turn: Turn, *, max_messages: int) -> SessionMemory:
    """Memory after `turn`: cited refs become `last_articles` (kept when nothing was cited, so a
    follow-up to a clarification still resolves), topics and messages roll forward."""
    discussed = _latest_unique(
        [*memory.articles_discussed, *turn.cited_refs], ARTICLES_DISCUSSED_MAX
    )
    topics = memory.recent_topics
    if turn.route_type not in {"chitchat", "out_of_scope", "ambiguous"}:
        topics = _latest_unique([*topics, turn.standalone_query], RECENT_TOPICS_MAX)
    messages = [
        *memory.messages,
        ChatMessage("user", turn.user),
        ChatMessage("assistant", turn.assistant),
    ]
    return SessionMemory(
        summary=memory.summary,
        last_articles=list(turn.cited_refs) or memory.last_articles,
        articles_discussed=discussed,
        recent_topics=topics,
        messages=messages[-max_messages:],
    )
