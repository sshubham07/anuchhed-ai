"""Ask the full pipeline from the command line (spec: llm-router-generation §3.10).

    python -m samvidhan.graph.cli ask "What does Article 21 say?"
    python -m samvidhan.graph.cli --fake-llm ask "Can police arrest me?"   # no API keys needed
    python -m samvidhan.graph.cli chat     # follow-ups share an in-process session

Streams the answer, then prints citations, the route, latency per node and the LLM calls made.
Real runs log every call to `llm_calls`; `--fake-llm` runs keep their rows in memory.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any, cast

import structlog

from samvidhan.core.config import Settings, get_settings
from samvidhan.core.errors import SamvidhanError
from samvidhan.core.ids import new_request_id
from samvidhan.core.logging import configure_logging, get_logger
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.db.repositories.corpus import CorpusRepository
from samvidhan.graph.builder import ChatGraph, build_graph
from samvidhan.graph.nodes import GraphDeps, load_prompts
from samvidhan.graph.state import ChatState
from samvidhan.llm.client import LLMClient
from samvidhan.llm.factory import create_llm_client
from samvidhan.llm.fake import demo_provider, fake_llm
from samvidhan.llm.recorder import MemoryCallRecorder
from samvidhan.memory.store import InMemorySessionStore
from samvidhan.retrieval.service import create_retrieval_service

log = get_logger(__name__)
out = sys.stdout.write
_MODEL_FIELDS = (
    "router_model",
    "router_fallback_model",
    "router_model_long",
    "answer_model",
    "answer_fallback_model",
    "answer_model_long",
    "answer_fallback_model_long",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.model_copy(update={"log_level": args.log_level}))
    try:
        asyncio.run(_run(settings, args))
    except SamvidhanError as exc:
        out(f"\nerror {exc.code}: {exc.message}\n")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="samvidhan.graph.cli")
    parser.add_argument("--fake-llm", action="store_true", help="no API keys: scripted FakeLLM")
    parser.add_argument("--debug", action="store_true", help="print the route JSON and trace")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING"])
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="answer one or more questions (each in a fresh session)")
    ask.add_argument("questions", nargs="+")
    sub.add_parser("chat", help="interactive chat; follow-ups use the conversation so far")
    return parser


async def _run(settings: Settings, args: argparse.Namespace) -> None:
    if args.fake_llm:  # label fake calls honestly in the printed usage
        settings = settings.model_copy(update=dict.fromkeys(_MODEL_FIELDS, "fake/model"))
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    usage = MemoryCallRecorder()
    llm: LLMClient = (
        fake_llm(demo_provider(), usage)
        if args.fake_llm
        else create_llm_client(settings, session_factory, also_record_to=usage)
    )
    try:
        async with session_factory() as session:
            edition = await CorpusRepository(session).active_version_date()
        out("Loading embedding and reranker models…\n")
        deps = GraphDeps(
            llm=llm,
            retrieval=create_retrieval_service(settings, session_factory),
            memory=InMemorySessionStore(max_messages=settings.raw_window),
            settings=settings,
            prompts=load_prompts(settings),
            edition=edition,
        )
        graph = build_graph(deps)
        if args.command == "ask":
            for question in args.questions:
                await _ask(graph, llm, usage, question, None, args.debug)
        else:
            await _chat(graph, llm, usage, args.debug)
    finally:
        await llm.recorder.drain()  # failed requests' rows too, before the pool closes
        await engine.dispose()


async def _chat(graph: ChatGraph, llm: LLMClient, usage: MemoryCallRecorder, debug: bool) -> None:
    session_id = uuid.uuid4()
    out("Ask about the Constitution of India. Empty line or Ctrl-D to quit.\n")
    while True:
        try:
            question = (await asyncio.to_thread(input, "\nyou> ")).strip()
        except EOFError:
            return
        if not question:
            return
        await _ask(graph, llm, usage, question, session_id, debug)


async def _ask(
    graph: ChatGraph,
    llm: LLMClient,
    usage: MemoryCallRecorder,
    question: str,
    session_id: uuid.UUID | None,
    debug: bool,
) -> None:
    request_id = new_request_id()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    if session_id is not None:
        structlog.contextvars.bind_contextvars(session_id=str(session_id))
    first_call = len(usage.records)
    started = time.perf_counter()
    state: dict[str, Any] = {}
    out(f"\nQ: {question}\nA: ")
    initial: ChatState = {"request_id": request_id, "session_id": session_id, "message": question}
    async for mode, raw in graph.astream(initial, stream_mode=["custom", "values"]):
        chunk = cast(dict[str, Any], raw)
        if (
            mode == "custom" and chunk.get("type") == "token"
        ):  # every visible text, incl. disclaimer
            out(chunk["text"])
            sys.stdout.flush()
        elif mode == "values":
            state = chunk
    total_ms = round((time.perf_counter() - started) * 1000)
    await llm.recorder.drain()
    _report(state, total_ms, usage.records[first_call:], debug)
    structlog.contextvars.unbind_contextvars("request_id", "session_id")


def _report(state: dict[str, Any], total_ms: int, calls: Sequence[Any], debug: bool) -> None:
    out("\n")
    if state.get("invalid_citations"):
        out(f"(removed citations not in the retrieved text: {state['invalid_citations']})\n")
    citations = state.get("citations", [])
    if citations:
        out("\nCitations:\n")
        for c in citations:
            out(f"  [{c.label}] {c.title or ''}\n")
    route = state["route"]
    latency = {**state.get("latency_ms", {}), "total": total_ms}
    out(
        f"\nroute={route.type} style={route.answer_style} refs={route.refs} "
        f"fallback={route.fallback} low_confidence={state.get('low_confidence', False)}\n"
        f"latency_ms={latency}\n"
    )
    for call in calls:
        out(
            f"llm_call purpose={call.purpose} model={call.model} status={call.status} "
            f"tokens={call.input_tokens}/{call.output_tokens} latency_ms={call.latency_ms}"
            f"{f' error={call.error_code}' if call.error_code else ''}\n"
        )
    if debug:
        out("\nroute: " + route.model_dump_json(indent=2) + "\n")
        if "retrieval" in state:
            out("trace: " + json.dumps(state["retrieval"].trace, indent=2, default=str) + "\n")
        if state.get("citation_stats"):
            out(f"citation_stats: {state['citation_stats']}\n")
        if "memory" in state:
            out(f"memory: {json.dumps(asdict(state['memory']), default=str)[:600]}\n")


if __name__ == "__main__":
    sys.exit(main())
