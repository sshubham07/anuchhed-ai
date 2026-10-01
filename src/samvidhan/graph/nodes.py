"""Graph nodes (HLD §8.1.1, ADR-0011): thin async wrappers over memory/, query/, retrieval/ and
generation/. Each takes the state and the injected deps and returns a partial state update.
No business logic lives here.
"""

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import date
from typing import Any

from langgraph.config import get_stream_writer

from samvidhan.core.config import Settings
from samvidhan.core.logging import get_logger
from samvidhan.generation import templates
from samvidhan.generation.answer import generate_answer
from samvidhan.generation.citations import disclaimer, validate_citations
from samvidhan.graph.state import ChatState
from samvidhan.llm.client import LLMClient
from samvidhan.llm.prompts import PromptTemplate, load_prompt
from samvidhan.memory.store import MemoryStore
from samvidhan.memory.types import SessionMemory, Turn
from samvidhan.query.decompose import retrieve_sub_queries
from samvidhan.query.hyde import hyde_passage
from samvidhan.query.router import TEMPLATE_ROUTES, RouteDecision, route
from samvidhan.retrieval.service import RetrievalService

log = get_logger(__name__)

Update = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Prompts:
    router: PromptTemplate
    hyde: PromptTemplate
    answer: PromptTemplate


def load_prompts(settings: Settings) -> Prompts:
    def load(version: str) -> PromptTemplate:
        return load_prompt(settings.prompts_dir, version)

    return Prompts(
        router=load(settings.router_prompt_version),
        hyde=load(settings.hyde_prompt_version),
        answer=load(settings.answer_prompt_version),
    )


@dataclass(frozen=True, slots=True)
class GraphDeps:
    """Built once at startup (API lifespan / CLI) and bound into the nodes by the builder."""

    llm: LLMClient
    retrieval: RetrievalService
    memory: MemoryStore
    settings: Settings
    prompts: Prompts
    edition: date | None = None


def _route(state: ChatState) -> RouteDecision:
    return state["route"]


async def load_memory(state: ChatState, deps: GraphDeps) -> Update:
    return {"memory": await deps.memory.load(state.get("session_id"))}


async def route_message(state: ChatState, deps: GraphDeps) -> Update:
    decision = await route(
        deps.llm,
        deps.settings,
        deps.prompts.router,
        state["message"],
        state.get("memory", SessionMemory()),
    )
    return {"route": decision}


async def hyde(state: ChatState, deps: GraphDeps) -> Update:
    passage = await hyde_passage(
        deps.llm, deps.settings, deps.prompts.hyde, _route(state).standalone_query
    )
    return {"hyde_passage": passage}


async def retrieve(state: ChatState, deps: GraphDeps) -> Update:
    decision = _route(state)
    result = await deps.retrieval.retrieve(
        decision.standalone_query, refs=decision.refs, dense_query=state.get("hyde_passage")
    )
    return {"retrieval": result, "low_confidence": result.low_confidence}


async def decompose(state: ChatState, deps: GraphDeps) -> Update:
    decision = _route(state)
    result = await retrieve_sub_queries(
        deps.retrieval,
        decision.sub_queries or [decision.standalone_query],
        refs=decision.refs,
        per_query_k=deps.settings.sub_query_k,
        cap=deps.settings.max_context_chunks,
    )
    return {"retrieval": result, "low_confidence": result.low_confidence}


async def generate(state: ChatState, deps: GraphDeps) -> Update:
    chunks = state["retrieval"].chunks
    if not chunks:  # nothing to ground an answer in: say so without an LLM call (rule 2)
        return {"answer": templates.NOT_FOUND, "context": [], "answer_model": None}
    write = get_stream_writer()
    draft = await generate_answer(
        deps.llm,
        deps.settings,
        deps.prompts.answer,
        message=state["message"],
        route=_route(state),
        chunks=chunks,
        memory=state.get("memory", SessionMemory()),
        low_confidence=state.get("low_confidence", False),
        on_token=lambda text: write({"type": "token", "text": text}),
    )
    return {
        "answer": draft.text,
        "context": draft.context,
        "answer_model": draft.result.model,
        "finish_reason": draft.result.finish_reason,
    }


async def check_citations(state: ChatState, deps: GraphDeps) -> Update:
    context = state.get("context", [])
    check = validate_citations(state["answer"], context)
    if context and not check.citations:
        log.warning("answer_without_citation", route_type=_route(state).type)
    return {
        "answer": f"{check.text}\n\n{disclaimer(deps.edition)}",
        "citations": check.citations,
        "citation_stats": check.stats,
        "invalid_citations": check.invalid_refs,
    }


async def respond_template(state: ChatState, deps: GraphDeps) -> Update:
    decision = _route(state)
    if decision.type == "ambiguous":
        answer = templates.clarification(decision.clarification_question)
    elif decision.type == "chitchat":
        answer = templates.chitchat(state["message"])
    else:
        answer = templates.OUT_OF_SCOPE
    return {"answer": answer, "citations": [], "context": []}


async def save_turn(state: ChatState, deps: GraphDeps) -> Update:
    decision = _route(state)
    cited = [c.ref for c in state.get("citations", [])]
    memory = await deps.memory.save_turn(
        state.get("session_id"),
        Turn(
            user=state["message"],
            assistant=state["answer"],
            standalone_query=decision.standalone_query,
            cited_refs=cited,
            route_type=decision.type,
        ),
    )
    log.info(
        "answer_completed",
        route_type=decision.type,
        cited_refs=cited,
        low_confidence=state.get("low_confidence", False),
        latency_breakdown=state.get("latency_ms", {}),
    )
    return {"memory": memory}


# Conditional-edge labels → node (the labels show on the Mermaid diagram).
ROUTE_EDGES: dict[Hashable, str] = {
    "lookup / simple": "retrieve",
    "conceptual": "hyde",
    "multi_part": "decompose",
    "ambiguous / out_of_scope / chitchat": "respond_template",
}


def next_after_route(state: ChatState) -> str:
    """Conditional edge on `route.type` (HLD §8.2 branch table); returns a `ROUTE_EDGES` key."""
    decision = _route(state)
    if decision.type in TEMPLATE_ROUTES:
        return "ambiguous / out_of_scope / chitchat"
    if decision.type == "multi_part":
        return "multi_part"
    if decision.type == "conceptual" or decision.use_hyde:
        return "conceptual"
    return "lookup / simple"
