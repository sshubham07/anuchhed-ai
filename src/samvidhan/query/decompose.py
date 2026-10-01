"""Multi-part retrieval: search per sub-query, then merge (spec: llm-router-generation §3.6,
HLD §8.2 branch table — "top-3 each, dedupe, cap 8")."""

import asyncio
import time
from collections.abc import Sequence

from samvidhan.core.logging import get_logger
from samvidhan.retrieval.service import RetrievalService
from samvidhan.retrieval.types import RetrievalResult, ScoredChunk

log = get_logger(__name__)


async def retrieve_sub_queries(
    service: RetrievalService,
    sub_queries: Sequence[str],
    *,
    refs: Sequence[str],
    per_query_k: int,
    cap: int,
) -> RetrievalResult:
    """Each sub-query is searched concurrently (refs pinned once, deduped in the merge)."""
    started = time.perf_counter()
    results = await asyncio.gather(*(service.retrieve(q, refs=refs) for q in sub_queries))
    merged = merge_results(sub_queries, results, per_query_k=per_query_k, cap=cap)
    merged.latency_ms["total"] = round((time.perf_counter() - started) * 1000)
    return merged


def merge_results(
    sub_queries: Sequence[str],
    results: Sequence[RetrievalResult],
    *,
    per_query_k: int,
    cap: int,
) -> RetrievalResult:
    """Pinned chunks first, then round-robin over each sub-query's top `per_query_k`, so every
    part of the question is represented before any part gets a second chunk."""
    pinned = [c for c in results[0].chunks if c.pinned] if results else []
    per_query = [[c for c in r.chunks if not c.pinned][:per_query_k] for r in results]
    merged: list[ScoredChunk] = []
    seen: set[str] = set()
    for chunk in [*pinned, *_round_robin(per_query)]:
        if chunk.id not in seen:
            seen.add(chunk.id)
            merged.append(chunk)
    if len(merged) > cap:
        log.warning("limit_applied", limit="context_chunks", requested=len(merged), allowed=cap)
        merged = merged[:cap]
    scores = [r.top_score for r in results if r.top_score is not None]
    low_confidence = not merged or all(r.low_confidence for r in results)
    return RetrievalResult(
        chunks=merged,
        candidates=[c for r in results for c in r.candidates],
        ranked=[c for r in results for c in r.ranked],
        refs=results[0].refs if results else [],
        top_score=max(scores) if scores else None,
        low_confidence=low_confidence,
        latency_ms={},
        trace={
            "mode": "decompose",
            "sub_queries": [
                {"query": q, **r.trace} for q, r in zip(sub_queries, results, strict=True)
            ],
            "final": [{"id": c.id, "pinned": c.pinned} for c in merged],
            "top_score": max(scores) if scores else None,
            "low_confidence": low_confidence,
        },
    )


def _round_robin(lists: Sequence[Sequence[ScoredChunk]]) -> list[ScoredChunk]:
    out: list[ScoredChunk] = []
    for depth in range(max((len(items) for items in lists), default=0)):
        out.extend(items[depth] for items in lists if depth < len(items))
    return out
