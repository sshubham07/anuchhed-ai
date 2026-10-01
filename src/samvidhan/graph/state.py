"""Graph state (HLD §8.1.1, spec: llm-router-generation §3.10).

Nodes return partial updates; only `latency_ms` merges (each node adds its own timing).
"""

import uuid
from typing import Annotated, TypedDict

from samvidhan.generation.citations import Citation
from samvidhan.memory.types import SessionMemory
from samvidhan.query.router import RouteDecision
from samvidhan.retrieval.types import RetrievalResult, ScoredChunk


def merge_latency(left: dict[str, int] | None, right: dict[str, int] | None) -> dict[str, int]:
    return {**(left or {}), **(right or {})}


class ChatState(TypedDict, total=False):
    request_id: str
    session_id: uuid.UUID | None
    message: str
    user_message_id: int | None  # stored user row; history loads messages before it
    started_at: float  # time.perf_counter() at graph start, for latency_ms["total"]
    memory: SessionMemory
    route: RouteDecision
    hyde_passage: str | None
    retrieval: RetrievalResult
    low_confidence: bool
    context: list[ScoredChunk]  # chunks actually sent to the answer LLM
    answer: str
    answer_model: str | None
    finish_reason: str | None
    citations: list[Citation]
    citation_stats: dict[str, int]
    invalid_citations: list[str]
    latency_ms: Annotated[dict[str, int], merge_latency]
    ttft_ms: int | None
    message_id: int | None  # stored assistant row (None outside the API)
