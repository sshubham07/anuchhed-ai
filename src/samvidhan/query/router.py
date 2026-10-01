"""Router / condense — LLM #1 (spec: llm-router-generation §3.5, HLD §8.2, ADR-0003/0004).

One small-LLM call returns a typed `RouteDecision`. Anything unusable — invalid JSON, a truncated
reply, the LLM being down — falls back to `simple` over the raw message (standards §4).
"""

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from samvidhan.core.config import Settings
from samvidhan.core.errors import LLMUnavailableError
from samvidhan.core.logging import get_logger
from samvidhan.llm.client import LLMClient
from samvidhan.llm.prompts import PromptTemplate
from samvidhan.llm.types import LLMRequest
from samvidhan.memory.types import SessionMemory

log = get_logger(__name__)

RouteType = Literal[
    "article_lookup", "simple", "multi_part", "conceptual", "ambiguous", "out_of_scope", "chitchat"
]
ROUTE_TYPES: tuple[RouteType, ...] = (
    "article_lookup",
    "simple",
    "multi_part",
    "conceptual",
    "ambiguous",
    "out_of_scope",
    "chitchat",
)
TEMPLATE_ROUTES: frozenset[str] = frozenset({"ambiguous", "out_of_scope", "chitchat"})
AnswerStyle = Literal["brief", "detailed", "exam"]

_HISTORY_CHARS = 500  # per message in the router prompt; assistant answers can be long
_LOG_CHARS = 200


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: RouteType
    standalone_query: str = ""
    article_refs: list[str] = Field(default_factory=list)
    schedule_refs: list[str] = Field(default_factory=list)
    sub_queries: list[str] = Field(default_factory=list)
    use_hyde: bool = False
    answer_style: AnswerStyle = "brief"
    clarification_question: str | None = None
    reason: str = ""
    fallback: bool = False  # set by code when the LLM output was unusable, never by the LLM

    @property
    def refs(self) -> list[str]:
        return [*self.article_refs, *self.schedule_refs]


def is_long(message: str, settings: Settings) -> bool:
    return len(message) > settings.long_query_chars


def render_memory(memory: SessionMemory) -> str:
    lines = []
    if memory.summary:
        lines.append(f"summary: {memory.summary}")
    lines.append(f"last_articles: {', '.join(memory.last_articles) or 'none'}")
    lines.append(f"articles_discussed: {', '.join(memory.articles_discussed) or 'none'}")
    return "\n".join(lines)


def render_history(memory: SessionMemory, limit: int) -> str:
    recent = memory.messages[-limit:] if limit > 0 else []
    if not recent:
        return "(no earlier messages)"
    return "\n".join(f"{m.role}: {m.content[:_HISTORY_CHARS]}" for m in recent)


async def route(
    llm: LLMClient,
    settings: Settings,
    prompt: PromptTemplate,
    message: str,
    memory: SessionMemory,
) -> RouteDecision:
    started = time.perf_counter()
    long = is_long(message, settings)
    request = LLMRequest(
        purpose="router",
        models=[
            settings.router_model_long if long else settings.router_model,
            settings.router_fallback_model,
        ],
        messages=prompt.render(
            memory=render_memory(memory),
            history=render_history(memory, settings.router_history_messages),
            message=message,
        ),
        max_tokens=settings.router_max_tokens,
        temperature=settings.router_temperature,
        timeout_s=settings.llm_timeout_long_s if long else settings.llm_timeout_s,
        json_mode=True,
        prompt_version=prompt.version,
    )
    try:
        result = await llm.complete(request)
    except LLMUnavailableError:
        decision = _fallback(message, "llm_unavailable")
    else:
        if result.truncated:
            decision = _fallback(message, "truncated")
        else:
            decision = _parse(result.text, message)
    decision = postprocess(decision, message, settings)
    log.info(
        "router_completed",
        route_type=decision.type,
        article_refs=decision.article_refs,
        schedule_refs=decision.schedule_refs,
        n_sub_queries=len(decision.sub_queries),
        use_hyde=decision.use_hyde,
        answer_style=decision.answer_style,
        fallback=decision.fallback,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    return decision


def _parse(text: str, message: str) -> RouteDecision:
    try:
        return RouteDecision.model_validate_json(_strip_fences(text))
    except ValidationError:
        return _fallback(message, "invalid_json")


def _strip_fences(text: str) -> str:
    """Some models wrap JSON in ``` fences even in JSON mode."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    return stripped


def _fallback(message: str, reason: str) -> RouteDecision:
    log.warning("router_fallback", reason=reason, standalone_query=message[:_LOG_CHARS])
    return RouteDecision(type="simple", standalone_query=message, fallback=True, reason=reason)


def postprocess(decision: RouteDecision, message: str, settings: Settings) -> RouteDecision:
    """Deterministic clean-up of the LLM's decision (spec §3.5)."""
    updates: dict[str, object] = {}
    if not decision.standalone_query.strip():
        updates["standalone_query"] = message
    sub_queries = [q.strip() for q in decision.sub_queries if q.strip()]
    cap = settings.max_sub_queries_long if is_long(message, settings) else settings.max_sub_queries
    if len(sub_queries) > cap:
        log.warning("limit_applied", limit="sub_queries", requested=len(sub_queries), allowed=cap)
        sub_queries = sub_queries[:cap]
    if decision.type != "multi_part":
        sub_queries = []
    updates["sub_queries"] = sub_queries
    if decision.type == "article_lookup" and not decision.refs:
        updates["type"] = "simple"
    if decision.type == "ambiguous" and not (decision.clarification_question or "").strip():
        updates["clarification_question"] = None  # the template supplies the default
    return decision.model_copy(update=updates)
