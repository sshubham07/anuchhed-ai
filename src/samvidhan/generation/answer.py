"""Answer generation — LLM #2 (spec: llm-router-generation §3.7, HLD §8.5).

Builds the grounded prompt from the retrieved chunks and streams the answer. Grounding rules live
in `prompts/answer.vN.md`; this module only formats data into it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

from samvidhan.core.config import Settings
from samvidhan.core.logging import get_logger
from samvidhan.generation.citations import TRUNCATION_NOTE, label
from samvidhan.llm.client import LLMClient
from samvidhan.llm.prompts import PromptTemplate
from samvidhan.llm.types import LLMRequest, LLMResult, Message
from samvidhan.memory.types import SessionMemory
from samvidhan.query.router import RouteDecision
from samvidhan.retrieval.types import ScoredChunk

log = get_logger(__name__)

CHARS_PER_TOKEN = 4  # estimate for the context-token cap; exact counts aren't needed for a cap
_HISTORY_CHARS = 800
LOW_CONFIDENCE_NOTE = (
    "<retrieval_note>Search found only weak matches for this question. If the excerpts do not "
    "clearly answer it, say that the text of the Constitution provided may not cover it."
    "</retrieval_note>"
)


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    text: str  # includes the truncation note when cut off
    result: LLMResult
    context: list[ScoredChunk]  # chunks actually sent (after the token cap)


def fit_context(chunks: Sequence[ScoredChunk], max_tokens: int) -> list[ScoredChunk]:
    """Chunks in order until the estimated token budget is spent (the first always fits)."""
    kept: list[ScoredChunk] = []
    used = 0
    for chunk in chunks:
        cost = len(chunk.embed_text) // CHARS_PER_TOKEN + 1
        if kept and used + cost > max_tokens:
            log.warning(
                "limit_applied", limit="context_tokens", requested=len(chunks), allowed=len(kept)
            )
            break
        kept.append(chunk)
        used += cost
    return kept


def render_excerpts(chunks: Sequence[ScoredChunk]) -> str:
    parts = []
    for chunk in chunks:
        cite = label(chunk.ref) if chunk.ref else chunk.id
        title = escape(chunk.article_title or "", quote=True)
        parts.append(
            f'<excerpt id="{chunk.id}" cite="{cite}" title="{title}">\n'
            f"{chunk.embed_text.strip()}\n</excerpt>"
        )
    return "\n".join(parts)


def render_conversation(memory: SessionMemory, limit: int) -> str:
    lines = [f"Summary of earlier conversation: {memory.summary}"] if memory.summary else []
    recent = memory.messages[-limit:] if limit > 0 else []
    lines += [f"{m.role}: {m.content[:_HISTORY_CHARS]}" for m in recent]
    return "\n".join(lines) or "(new conversation)"


def build_messages(
    prompt: PromptTemplate,
    settings: Settings,
    *,
    message: str,
    route: RouteDecision,
    context: Sequence[ScoredChunk],
    memory: SessionMemory,
    low_confidence: bool,
) -> list[Message]:
    return prompt.render(
        conversation=render_conversation(memory, settings.answer_history_messages),
        articles_discussed=", ".join(memory.articles_discussed) or "none",
        retrieval_note=LOW_CONFIDENCE_NOTE if low_confidence else "",
        excerpts=render_excerpts(context),
        message=message,
        standalone_query=route.standalone_query,
        answer_style=route.answer_style,
    )


def answer_request(
    settings: Settings, prompt: PromptTemplate, route: RouteDecision, messages: list[Message]
) -> LLMRequest:
    """`brief` → the 70B answer model; `detailed` / `exam` → the long-answer model (ADR-0012)."""
    long = route.answer_style != "brief"
    models = (
        [settings.answer_model_long, settings.answer_fallback_model_long]
        if long
        else [settings.answer_model, settings.answer_fallback_model]
    )
    return LLMRequest(
        purpose="answer",
        models=models,
        messages=messages,
        max_tokens=settings.answer_max_tokens_long if long else settings.answer_max_tokens,
        temperature=settings.answer_temperature,
        timeout_s=settings.llm_timeout_long_s if long else settings.llm_timeout_s,
        prompt_version=prompt.version,
    )


async def generate_answer(
    llm: LLMClient,
    settings: Settings,
    prompt: PromptTemplate,
    *,
    message: str,
    route: RouteDecision,
    chunks: Sequence[ScoredChunk],
    memory: SessionMemory,
    low_confidence: bool,
    on_token: Callable[[str], None] | None = None,
) -> AnswerDraft:
    context = fit_context(chunks, settings.max_context_tokens)
    messages = build_messages(
        prompt,
        settings,
        message=message,
        route=route,
        context=context,
        memory=memory,
        low_confidence=low_confidence,
    )
    request = answer_request(settings, prompt, route, messages)
    parts: list[str] = []
    result: LLMResult | None = None
    async for item in llm.stream(request):
        if isinstance(item, LLMResult):
            result = item
        else:
            parts.append(item)
            if on_token is not None:
                on_token(item)
    if result is None:  # LLMClient always ends a stream with its result
        raise RuntimeError("LLM stream ended without a result")
    text = "".join(parts).strip()
    if result.truncated:
        log.warning(
            "answer_truncated",
            answer_style=route.answer_style,
            max_tokens=request.max_tokens,
            model=result.model,
        )
        text = f"{text}…\n\n_{TRUNCATION_NOTE}_"
    return AnswerDraft(text=text, result=result, context=context)
