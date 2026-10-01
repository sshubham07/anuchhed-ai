"""Retrieval service: pinned + dense + lexical → RRF → rerank (spec: retrieval §3, HLD §8.3).

Stateless: takes a standalone query and refs, never chat history (AGENTS.md rule 3).
"""

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from samvidhan.core.config import Settings
from samvidhan.core.errors import ServiceUnavailableError
from samvidhan.core.logging import get_logger
from samvidhan.ingestion.embed import Embedder
from samvidhan.retrieval.dense import dense_search
from samvidhan.retrieval.fusion import rrf
from samvidhan.retrieval.lexical import lexical_search
from samvidhan.retrieval.lookup import fetch_pinned, load_known_refs, normalize_refs
from samvidhan.retrieval.rerank import Reranker
from samvidhan.retrieval.types import RetrievalMode, RetrievalResult, ScoredChunk

log = get_logger(__name__)

_QUERY_LOG_CHARS = 200


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class RetrievalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        reranker: Reranker,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._reranker = reranker
        self._settings = settings
        self._known_refs: frozenset[str] | None = None

    async def known_refs(self) -> frozenset[str]:
        """Refs in the active document, loaded once per process (re-activating a document
        needs a restart). Empty means no active corpus."""
        if self._known_refs is None:
            async with self._session_factory() as session:
                known = await load_known_refs(session)
            if not known:
                raise ServiceUnavailableError("No active corpus; run `ingest --activate`")
            self._known_refs = known
        return self._known_refs

    async def validate_refs(self, raw_refs: Sequence[str]) -> list[str]:
        """Normalise, drop unknown refs (logged) and cap at MAX_ARTICLE_REFS."""
        refs, rejected = normalize_refs(raw_refs)
        known = await self.known_refs()
        for ref in [*rejected, *(r for r in refs if r not in known)]:
            log.warning("unknown_ref_dropped", ref=ref)
        refs = [r for r in refs if r in known]
        cap = self._settings.max_article_refs
        if len(refs) > cap:
            log.warning("limit_applied", limit="article_refs", requested=len(refs), allowed=cap)
            refs = refs[:cap]
        return refs

    async def retrieve(
        self,
        query: str,
        *,
        refs: Sequence[str] = (),
        dense_query: str | None = None,
        mode: RetrievalMode = "hybrid_rerank",
        lexical_match_all: bool = False,
    ) -> RetrievalResult:
        """`dense_query` (a HyDE passage) replaces `query` for the dense leg only; lexical search
        and rerank keep `query`. `mode` and `lexical_match_all` exist for the ablation."""
        settings = self._settings
        started = time.perf_counter()
        latency: dict[str, int] = {}
        valid_refs = await self.validate_refs(refs)

        async def pinned_leg() -> list[ScoredChunk]:
            if not valid_refs:
                return []  # no session, no pool connection
            leg_started = time.perf_counter()
            async with self._session_factory() as session:
                chunks = await fetch_pinned(session, valid_refs)
            latency["pinned"] = _ms(leg_started)
            return chunks

        async def dense_leg() -> list[ScoredChunk]:
            if mode == "lexical":
                return []
            leg_started = time.perf_counter()
            vectors = await asyncio.to_thread(self._embedder.embed, [dense_query or query])
            latency["embed"] = _ms(leg_started)
            leg_started = time.perf_counter()
            async with self._session_factory() as session:
                chunks = await dense_search(session, vectors[0], settings.dense_k)
            latency["dense"] = _ms(leg_started)
            return chunks

        async def lexical_leg() -> list[ScoredChunk]:
            if mode == "dense":
                return []
            leg_started = time.perf_counter()
            async with self._session_factory() as session:
                chunks = await lexical_search(
                    session, query, settings.lexical_k, match_all=lexical_match_all
                )
            latency["lexical"] = _ms(leg_started)
            return chunks

        pinned, dense, lexical = await asyncio.gather(pinned_leg(), dense_leg(), lexical_leg())

        fusion_started = time.perf_counter()
        legs = {"dense": dense, "lexical": lexical}
        candidates = rrf(
            {name: leg for name, leg in legs.items() if leg},
            k=settings.rrf_k,
            limit=settings.rerank_candidates,
        )
        latency["fusion"] = _ms(fusion_started)
        latency["retrieval"] = _ms(started)
        log.info(
            "retrieval_completed",
            mode=mode,
            n_pinned=len(pinned),
            n_dense=len(dense),
            n_lexical=len(lexical),
            n_candidates=len(candidates),
            duration_ms=latency["retrieval"],
        )

        top_score: float | None = None
        ranked = list(candidates)
        if mode == "hybrid_rerank" and candidates:
            rerank_started = time.perf_counter()
            reranked = await self._rerank(query, candidates)
            latency["rerank"] = _ms(rerank_started)
            if reranked is not None:
                ranked = reranked
                top_score = ranked[0].score
                log.info(
                    "rerank_completed",
                    top_score=round(top_score, 4),
                    final_ids=[c.id for c in ranked[: settings.final_k]],
                    duration_ms=latency["rerank"],
                )

        chunks = self._assemble(pinned, ranked[: settings.final_k])
        # Nothing retrieved counts as weak too, so the answer step says so (AGENTS.md rule 2).
        low_confidence = not chunks or (
            top_score is not None and top_score < settings.low_confidence_threshold and not pinned
        )
        if low_confidence:
            log.warning(
                "low_confidence_retrieval",
                top_score=None if top_score is None else round(top_score, 4),
                threshold=settings.low_confidence_threshold,
                standalone_query=query[:_QUERY_LOG_CHARS],
            )
        latency["total"] = _ms(started)
        trace = self._trace(
            mode, valid_refs, pinned, candidates, chunks, top_score, low_confidence, latency
        )
        if dense_query:
            trace["dense_query"] = "hyde"
        return RetrievalResult(
            chunks=chunks,
            candidates=candidates,
            ranked=ranked,
            refs=valid_refs,
            top_score=top_score,
            low_confidence=low_confidence,
            latency_ms=latency,
            trace=trace,
        )

    async def _rerank(
        self, query: str, candidates: Sequence[ScoredChunk]
    ) -> list[ScoredChunk] | None:
        """Candidates by rerank score, or None when the reranker fails (keep RRF order)."""
        try:
            scores = await asyncio.to_thread(
                self._reranker.score, query, [c.embed_text for c in candidates]
            )
        except Exception as exc:  # any model failure degrades to RRF order (spec §3.7)
            log.warning("rerank_skipped", error=repr(exc), exc_info=True)
            return None
        order = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
        return [
            chunk.with_stage("rerank", rank, score)
            for rank, (chunk, score) in enumerate(order, start=1)
        ]

    def _assemble(
        self, pinned: Sequence[ScoredChunk], ranked: Sequence[ScoredChunk]
    ) -> list[ScoredChunk]:
        """Pinned first (reading order), then ranked, deduped and capped."""
        pinned_ids = {c.id for c in pinned}
        merged = [*pinned, *(c for c in ranked if c.id not in pinned_ids)]
        cap = self._settings.max_context_chunks
        if len(merged) > cap:
            log.warning("limit_applied", limit="context_chunks", requested=len(merged), allowed=cap)
        return merged[:cap]

    @staticmethod
    def _trace(
        mode: RetrievalMode,
        refs: Sequence[str],
        pinned: Sequence[ScoredChunk],
        candidates: Sequence[ScoredChunk],
        chunks: Sequence[ScoredChunk],
        top_score: float | None,
        low_confidence: bool,
        latency: dict[str, int],
    ) -> dict[str, Any]:
        def rounded(scores: dict[str, float]) -> dict[str, float]:
            return {stage: round(value, 4) for stage, value in scores.items()}

        return {
            "mode": mode,
            "refs": list(refs),
            "pinned": [c.id for c in pinned],
            "candidates": [
                {"id": c.id, "ranks": c.ranks, "scores": rounded(c.scores)} for c in candidates
            ],
            "final": [
                {"id": c.id, "pinned": c.pinned, "ranks": c.ranks, "scores": rounded(c.scores)}
                for c in chunks
            ],
            "top_score": None if top_score is None else round(top_score, 4),
            "low_confidence": low_confidence,
            "latency_ms": dict(latency),
        }


def create_retrieval_service(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> RetrievalService:
    """Composition root helper: loads the local embedder and reranker (slow; call once)."""
    from samvidhan.ingestion.embed import BgeM3Embedder
    from samvidhan.retrieval.rerank import BgeReranker

    embedder = BgeM3Embedder(
        settings.embed_model,
        device=settings.model_device,
        batch_size=settings.embed_batch_size,
        max_length=settings.embed_max_length,
    )
    reranker = BgeReranker(
        settings.rerank_model, device=settings.model_device, max_length=settings.rerank_max_length
    )
    return RetrievalService(session_factory, embedder, reranker, settings)
