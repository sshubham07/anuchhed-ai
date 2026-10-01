"""`POST /v1/chat`: runs the LangGraph pipeline and streams it as SSE, or returns JSON
(HLD §8.1, §11; spec: api-sessions-memory §3.5).

HTTP concerns stay here (HLD §8.1 steps A–C): rate limit, input checks, session lookup, persisting
the user message. Steps B–H run in the graph; its stream is mapped to events:
route decided → `meta`, custom tokens → `token`, citations validated → `citations`, end → `done`.
"""

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.api.concurrency import SlotStreamingResponse
from samvidhan.api.deps import get_app_settings, get_client_ip_hash, get_db, get_services
from samvidhan.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationOut,
    ErrorEnvelope,
    RouteOut,
)
from samvidhan.api.services import AppServices
from samvidhan.core.config import Settings
from samvidhan.core.errors import (
    EmptyMessageError,
    MessageTooLongError,
    NotFoundError,
    RateLimitedError,
    SamvidhanError,
    SessionFullError,
)
from samvidhan.core.logging import get_logger
from samvidhan.db.repositories.chat import MessageRepository, SessionRepository
from samvidhan.generation.citations import label
from samvidhan.graph.builder import ChatGraph
from samvidhan.graph.state import ChatState

log = get_logger(__name__)

router = APIRouter(tags=["chat"])

Event = tuple[str, dict[str, Any]]


async def graph_events(
    graph: ChatGraph, initial: ChatState, *, debug: bool = False
) -> AsyncGenerator[Event, None]:
    """Map the graph's `custom` + `updates` stream to API events, always in the order
    `meta → token… → citations → done`; a failure ends the stream with one `error` event.
    `debug` adds the route and scored chunks to `done` for the UI's debug panel (`DEBUG_UI`)."""
    latency: dict[str, int] = {}
    final: dict[str, Any] = {}
    citations_sent = False
    try:
        async for mode, raw in graph.astream(initial, stream_mode=["custom", "updates"]):
            chunk = cast(dict[str, Any], raw)
            if mode == "custom":
                if chunk.get("type") == "token":
                    yield "token", {"text": chunk["text"]}
                continue
            for node, update in chunk.items():
                if not update:
                    continue
                latency.update(update.get("latency_ms", {}))
                final.update({k: v for k, v in update.items() if k != "latency_ms"})
                if node == "route":
                    yield "meta", _meta(initial, update["route"])
                elif node == "validate_citations":
                    citations_sent = True
                    yield "citations", _citations(update)
    except SamvidhanError as exc:
        log.warning("chat_failed", error_code=exc.code)
        yield "error", _error(exc.code, exc.message, initial)
        return
    except Exception:
        log.error("request_failed", error_code="INTERNAL_ERROR", exc_info=True)
        yield "error", _error("INTERNAL_ERROR", "Internal server error", initial)
        return
    if not citations_sent:  # templated replies and "not covered": no citations, same event order
        yield "citations", _citations({})
    done = {
        "message_id": final.get("message_id"),
        "answer": final.get("answer", ""),
        "low_confidence": final.get("low_confidence", False),
        "latency_ms": latency,
    }
    if debug:
        done["debug"] = _debug(final)
    yield "done", done


def _debug(final: dict[str, Any]) -> dict[str, Any]:
    """Route + chunks behind the answer, with per-stage scores (spec: api-sessions-memory §10)."""
    route = final.get("route")
    retrieval = final.get("retrieval")
    chunks = final.get("context") or (retrieval.chunks if retrieval is not None else [])
    return {
        "route": route.model_dump(mode="json") if route is not None else None,
        "chunks": [
            {
                "ref": c.ref,
                "label": label(c.ref) if c.ref else None,
                "title": c.article_title,
                "pinned": c.pinned,
                "scores": {k: round(float(v), 4) for k, v in c.scores.items()},
            }
            for c in chunks
        ],
        "citation_stats": final.get("citation_stats", {}),
        "invalid_citations": final.get("invalid_citations", []),
    }


def _citations(update: dict[str, Any]) -> dict[str, Any]:
    return {
        "citations": [_citation(c) for c in update.get("citations", [])],
        "invalid": update.get("invalid_citations", []),
    }


def _meta(initial: ChatState, route: Any) -> dict[str, Any]:
    return {
        "request_id": initial.get("request_id"),
        "session_id": str(initial.get("session_id")),
        "route_type": route.type,
        "standalone_query": route.standalone_query,
        "refs": route.refs,
        "answer_style": route.answer_style,
    }


def _citation(citation: Any) -> dict[str, Any]:
    return {"ref": citation.ref, "label": citation.label, "title": citation.title}


def _error(code: str, message: str, initial: ChatState) -> dict[str, Any]:
    return {"code": code, "message": message, "request_id": initial.get("request_id")}


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def collect(events: AsyncIterator[Event]) -> ChatResponse:
    """`stream=false`: run the same events to the end and build the JSON body."""
    meta: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = []
    async for event, data in events:
        if event == "meta":
            meta = data
        elif event == "citations":
            citations = data["citations"]
        elif event == "error":
            raise _ERRORS.get(data["code"], SamvidhanError)(data["message"])
        elif event == "done":
            return ChatResponse(
                message_id=data["message_id"],
                answer=data["answer"],
                citations=[CitationOut(**c) for c in citations],
                route=(
                    RouteOut(
                        type=meta["route_type"],
                        standalone_query=meta["standalone_query"],
                        refs=meta["refs"],
                        answer_style=meta["answer_style"],
                    )
                    if meta
                    else None
                ),
                low_confidence=data["low_confidence"],
                latency_ms=data["latency_ms"],
            )
    raise SamvidhanError()  # the generator always ends with `done` or `error`


def _error_classes() -> dict[str, type[SamvidhanError]]:
    """Error code → class, so JSON mode returns the same status as the original error."""
    classes: dict[str, type[SamvidhanError]] = {}
    pending: list[type[SamvidhanError]] = [SamvidhanError]
    while pending:
        cls = pending.pop()
        classes.setdefault(cls.code, cls)
        pending.extend(cls.__subclasses__())
    return classes


# RateLimitedError needs `retry_after_s`; the graph never raises it, so it maps to the generic 500.
_ERRORS = {code: cls for code, cls in _error_classes().items() if cls is not RateLimitedError}


def _check_message(message: str, settings: Settings) -> str:
    text = message.strip()
    if not text:
        log.info("input_rejected", reason="empty", length=len(message), limit=None)
        raise EmptyMessageError()
    if len(text) > settings.max_message_chars:
        log.info(
            "input_rejected", reason="too_long", length=len(text), limit=settings.max_message_chars
        )
        raise MessageTooLongError(
            f"The message is too long ({len(text)} characters); "
            f"the limit is {settings.max_message_chars}."
        )
    return text


@router.post(
    "/chat",
    response_model=None,
    responses={
        200: {"model": ChatResponse, "content": {"text/event-stream": {}}},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        429: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    services: Annotated[AppServices, Depends(get_services)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    ip_hash: Annotated[str, Depends(get_client_ip_hash)],
) -> SlotStreamingResponse | ChatResponse:
    # Bound for the rest of the request: logs and `llm_calls.session_id` (llm/client.py).
    structlog.contextvars.bind_contextvars(session_id=str(body.session_id))
    await services.limiter.check_chat(ip_hash, body.session_id)
    message = _check_message(body.message, settings)
    sessions = SessionRepository(db)
    if await sessions.get(body.session_id) is None:
        raise NotFoundError("Session not found")
    # Check-then-insert: concurrent requests may overshoot by a few; the session rate limit
    # keeps that small.
    n_messages = await sessions.count_messages(body.session_id)
    if n_messages >= settings.max_messages_per_session:
        log.info(
            "input_rejected",
            reason="session_full",
            length=n_messages,
            limit=settings.max_messages_per_session,
        )
        raise SessionFullError()

    slot = services.streams.acquire()  # 503 BUSY before anything is stored or called
    streaming = False
    try:
        log.info("chat_request_received", message_len=len(message), client_ip_hash=ip_hash[:16])
        request_id: str = request.state.request_id
        user_row = await MessageRepository(db).add_user(body.session_id, request_id, message)
        await db.commit()  # HLD §8.1 step C: the question is stored even if the pipeline fails

        initial: ChatState = {
            "request_id": request_id,
            "session_id": body.session_id,
            "message": message,
            "user_message_id": user_row.id,
            "started_at": time.perf_counter(),
            "answer_style_override": body.answer_style,
        }
        events = graph_events(services.graph, initial, debug=settings.debug_ui)
        if not body.stream:
            return await collect(events)

        async def body_stream() -> AsyncIterator[str]:
            # Closing the generator on client disconnect cancels the graph: no answer is saved.
            async with aclosing(events):
                async for event, data in events:
                    yield sse(event, data)

        streaming = True  # the response releases the slot when it finishes
        return SlotStreamingResponse(
            body_stream(),
            slot,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    finally:
        if not streaming:
            slot.release()
