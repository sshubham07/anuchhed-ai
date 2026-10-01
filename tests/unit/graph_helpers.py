"""Graph fakes shared by the graph and API unit tests: a stub retriever and scripted router."""

import json
from collections.abc import Sequence
from datetime import date
from typing import Any, cast

from samvidhan.core.config import Settings
from samvidhan.graph.nodes import GraphDeps, load_prompts
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.recorder import MemoryCallRecorder
from samvidhan.memory.store import InMemorySessionStore
from samvidhan.retrieval.service import RetrievalService
from samvidhan.retrieval.types import RetrievalResult
from tests.unit.retrieval_helpers import chunk

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
