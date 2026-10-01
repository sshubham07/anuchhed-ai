"""Graph: conditional edges, nodes, and the compiled graph over a stub retriever (spec §3.10).

The Postgres-backed end-to-end run is `tests/integration/test_graph_e2e.py`.
"""

import json
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, cast

import pytest

from samvidhan.core.config import Settings
from samvidhan.generation import templates
from samvidhan.graph import nodes
from samvidhan.graph.builder import build_graph
from samvidhan.graph.nodes import GraphDeps, load_prompts
from samvidhan.graph.state import ChatState
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.recorder import MemoryCallRecorder
from samvidhan.memory.store import InMemorySessionStore
from samvidhan.query.router import RouteDecision
from samvidhan.retrieval.service import RetrievalService
from samvidhan.retrieval.types import RetrievalResult
from tests.unit.retrieval_helpers import chunk

TEMPLATE = "ambiguous / out_of_scope / chitchat"
ART21 = chunk("art-21#0", article_no="21", text="Article 21: no person shall be deprived of life")
ART22 = chunk("art-22#0", article_no="22", text="Article 22: grounds of arrest")


class StubRetrieval:
    """Records calls; returns fixed chunks (empty for queries containing 'nothing')."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self, query: str, *, refs: Sequence[str] = (), dense_query: str | None = None
    ) -> RetrievalResult:
        self.calls.append({"query": query, "refs": list(refs), "dense_query": dense_query})
        chunks = [] if "nothing" in query else [ART21, ART22]
        return RetrievalResult(
            chunks=chunks,
            candidates=chunks,
            ranked=chunks,
            refs=list(refs),
            top_score=0.9 if chunks else None,
            low_confidence=not chunks,
            latency_ms={"total": 1},
            trace={"mode": "stub"},
        )


def router_reply(**fields: Any) -> str:
    return json.dumps({"type": "simple", "standalone_query": "q", **fields})


def make(
    settings: Settings, provider: FakeProvider
) -> tuple[GraphDeps, StubRetrieval, MemoryCallRecorder]:
    retrieval, recorder = StubRetrieval(), MemoryCallRecorder()
    deps = GraphDeps(
        llm=fake_llm(provider, recorder),
        retrieval=cast(RetrievalService, retrieval),
        memory=InMemorySessionStore(max_messages=12),
        settings=settings,
        prompts=load_prompts(settings),
        edition=date(2024, 5, 1),
    )
    return deps, retrieval, recorder


@pytest.mark.parametrize(
    ("decision", "edge"),
    [
        (RouteDecision(type="article_lookup", standalone_query="q"), "lookup / simple"),
        (RouteDecision(type="simple", standalone_query="q"), "lookup / simple"),
        (RouteDecision(type="simple", standalone_query="q", use_hyde=True), "conceptual"),
        (RouteDecision(type="conceptual", standalone_query="q"), "conceptual"),
        (RouteDecision(type="multi_part", standalone_query="q"), "multi_part"),
        (RouteDecision(type="ambiguous", standalone_query="q"), TEMPLATE),
        (RouteDecision(type="out_of_scope", standalone_query="q"), TEMPLATE),
        (RouteDecision(type="chitchat", standalone_query="q"), TEMPLATE),
    ],
)  # fmt: skip
def test_next_after_route(decision: RouteDecision, edge: str) -> None:
    assert nodes.next_after_route({"route": decision}) == edge
    assert nodes.ROUTE_EDGES[edge] in {"retrieve", "hyde", "decompose", "respond_template"}


async def test_generate_without_chunks_skips_the_llm(settings: Settings) -> None:
    deps, _, recorder = make(settings, FakeProvider())
    state: ChatState = {
        "message": "q",
        "route": RouteDecision(type="simple", standalone_query="nothing"),
        "retrieval": await StubRetrieval().retrieve("nothing"),
    }
    update = await nodes.generate(state, deps)
    assert update["answer"] == templates.NOT_FOUND and recorder.records == []


async def test_check_citations_appends_disclaimer_and_drops_invalid(settings: Settings) -> None:
    deps, _, _ = make(settings, FakeProvider())
    state: ChatState = {
        "route": RouteDecision(type="simple", standalone_query="q"),
        "answer": "Life [Art. 21]. Wi-Fi [Art. 999].",
        "context": [ART21],
    }
    update = await nodes.check_citations(state, deps)
    assert update["answer"].startswith("Life [Art. 21]. Wi-Fi.\n\n_Informational only")
    assert "(as on 1 May 2024)" in update["answer"]
    assert [c.ref for c in update["citations"]] == ["21"]
    assert update["invalid_citations"] == ["999"]
    assert update["citation_stats"] == {"n_raw": 2, "n_valid": 1}


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (RouteDecision(type="ambiguous", standalone_query="q", clarification_question="Which?"),
         "Which?"),
        (RouteDecision(type="out_of_scope", standalone_query="q"), templates.OUT_OF_SCOPE),
    ],
)  # fmt: skip
async def test_respond_template(settings: Settings, decision: RouteDecision, expected: str) -> None:
    deps, _, _ = make(settings, FakeProvider())
    update = await nodes.respond_template({"message": "m", "route": decision}, deps)
    assert update["answer"] == expected and update["citations"] == []


async def test_compiled_graph_simple_route_streams_and_cites(settings: Settings) -> None:
    provider = FakeProvider(
        {
            "router": router_reply(standalone_query="Can police arrest without reasons?"),
            "answer": "You must be told the grounds of arrest [Art. 22].",
        }
    )
    deps, retrieval, recorder = make(settings, provider)
    graph = build_graph(deps)
    tokens: list[str] = []
    final: dict[str, Any] = {}
    initial: ChatState = {"request_id": "r1", "session_id": None, "message": "arrest?"}
    async for mode, chunk_ in graph.astream(initial, stream_mode=["custom", "values"]):
        if mode == "custom":
            tokens.append(chunk_["text"])
        else:
            final = chunk_
    assert "".join(tokens) == "You must be told the grounds of arrest [Art. 22]."
    assert [c.ref for c in final["citations"]] == ["22"]
    assert final["answer"].endswith("Not legal advice._")
    assert retrieval.calls == [
        {"query": "Can police arrest without reasons?", "refs": [], "dense_query": None}
    ]
    assert [r.purpose for r in recorder.records] == ["router", "answer"]
    assert set(final["latency_ms"]) == {
        "load_memory", "route", "retrieve", "generate", "validate_citations", "save_turn"
    }  # fmt: skip


async def test_compiled_graph_conceptual_uses_hyde_for_the_dense_leg(settings: Settings) -> None:
    provider = FakeProvider(
        {
            "router": router_reply(
                type="conceptual", standalone_query="environment", use_hyde=True
            ),
            "hyde": "The State shall protect the environment.",
            "answer": "See [Art. 21].",
        }
    )
    deps, retrieval, recorder = make(settings, provider)
    final = await build_graph(deps).ainvoke({"message": "environment?", "session_id": None})
    assert retrieval.calls[0]["dense_query"] == "The State shall protect the environment."
    assert retrieval.calls[0]["query"] == "environment"
    assert [r.purpose for r in recorder.records] == ["router", "hyde", "answer"]
    assert final["citations"][0].ref == "21"


async def test_compiled_graph_multi_part_searches_each_sub_query(settings: Settings) -> None:
    provider = FakeProvider(
        {
            "router": router_reply(
                type="multi_part",
                standalone_query="compare 21 and 22",
                article_refs=["21", "22"],
                sub_queries=["What does Article 21 say?", "What does Article 22 say?"],
            ),
            "answer": "Article 21 [Art. 21] and Article 22 [Art. 22].",
        }
    )
    deps, retrieval, _ = make(settings, provider)
    final = await build_graph(deps).ainvoke({"message": "compare", "session_id": None})
    assert [c["query"] for c in retrieval.calls] == [
        "What does Article 21 say?",
        "What does Article 22 say?",
    ]
    assert all(c["refs"] == ["21", "22"] for c in retrieval.calls)
    assert [c.id for c in final["context"]] == ["art-21#0", "art-22#0"]
    assert final["retrieval"].trace["mode"] == "decompose"


async def test_compiled_graph_template_route_makes_one_llm_call(settings: Settings) -> None:
    provider = FakeProvider({"router": router_reply(type="out_of_scope")})
    deps, retrieval, recorder = make(settings, provider)
    final = await build_graph(deps).ainvoke({"message": "BNS punishment?", "session_id": None})
    assert final["answer"] == templates.OUT_OF_SCOPE
    assert retrieval.calls == [] and [r.purpose for r in recorder.records] == ["router"]


async def test_follow_up_sees_the_previous_turn(settings: Settings) -> None:
    replies = [
        router_reply(type="article_lookup", standalone_query="Article 21", article_refs=["21"]),
        router_reply(standalone_query="exceptions to Article 21", article_refs=["21"]),
    ]
    provider = FakeProvider({"router": replies, "answer": "Life [Art. 21]."})
    deps, _, _ = make(settings, provider)
    graph = build_graph(deps)
    session_id = uuid.uuid4()
    await graph.ainvoke({"message": "What does Article 21 say?", "session_id": session_id})
    final = await graph.ainvoke({"message": "What are its exceptions?", "session_id": session_id})
    second_router_prompt = provider.calls[2].messages[-1]["content"]  # router, answer, router
    assert "last_articles: 21" in second_router_prompt
    assert "user: What does Article 21 say?" in second_router_prompt
    assert final["memory"].articles_discussed == ["21"]
