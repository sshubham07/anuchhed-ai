"""Reciprocal Rank Fusion (spec: retrieval §3.5, HLD §8.3)."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from samvidhan.retrieval.types import ScoredChunk


def rrf(legs: Mapping[str, Sequence[ScoredChunk]], k: int, limit: int) -> list[ScoredChunk]:
    """Fuse ranked legs: `score = Σ 1/(k + rank)`, ranks 1-based.

    Each leg's rank/score stays on the merged chunk. Ties break by best single-leg rank, then id.
    """
    merged: dict[str, ScoredChunk] = {}
    fused: dict[str, float] = {}
    for leg in legs.values():
        for rank, chunk in enumerate(leg, start=1):
            fused[chunk.id] = fused.get(chunk.id, 0.0) + 1.0 / (k + rank)
            seen = merged.get(chunk.id)
            merged[chunk.id] = (
                chunk
                if seen is None
                else replace(
                    seen,
                    ranks={**seen.ranks, **chunk.ranks},
                    scores={**seen.scores, **chunk.scores},
                )
            )
    order = sorted(
        merged.values(),
        key=lambda c: (-fused[c.id], min(c.ranks.values(), default=0), c.id),
    )
    return [
        chunk.with_stage("rrf", rank, fused[chunk.id])
        for rank, chunk in enumerate(order[:limit], start=1)
    ]
