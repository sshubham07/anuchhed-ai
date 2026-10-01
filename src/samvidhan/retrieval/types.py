"""Retrieval data types (spec: retrieval §3.2)."""

from dataclasses import dataclass, field, replace
from typing import Any, Literal

RetrievalMode = Literal["dense", "lexical", "hybrid", "hybrid_rerank"]
RETRIEVAL_MODES: tuple[RetrievalMode, ...] = ("dense", "lexical", "hybrid", "hybrid_rerank")


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    id: str
    chunk_type: str
    seq: int
    article_no: str | None
    schedule_no: str | None
    appendix_no: str | None
    article_title: str | None
    text: str
    embed_text: str
    score: float = 0.0
    # 1-based rank and raw score per stage: dense / lexical / rrf / rerank
    ranks: dict[str, int] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    pinned: bool = False

    @property
    def ref(self) -> str | None:
        """Canonical ref this chunk belongs to (`21A`, `SCH-7`, `PREAMBLE`, `APP-I`)."""
        if self.article_no:
            return self.article_no
        if self.schedule_no:
            return f"SCH-{self.schedule_no}"
        if self.appendix_no:
            return f"APP-{self.appendix_no}"
        return "PREAMBLE" if self.chunk_type == "preamble" else None

    def with_stage(self, stage: str, rank: int, score: float) -> "ScoredChunk":
        """Copy with `stage`'s rank and score recorded and `score` set to it."""
        return replace(
            self,
            score=score,
            ranks={**self.ranks, stage: rank},
            scores={**self.scores, stage: score},
        )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[ScoredChunk]  # final context: pinned first, then reranked
    candidates: list[ScoredChunk]  # after fusion, before rerank
    ranked: list[ScoredChunk]  # all candidates in final order (reranked, or RRF order)
    refs: list[str]  # validated canonical refs that were pinned
    top_score: float | None  # best rerank score; None when rerank was off or skipped
    low_confidence: bool
    latency_ms: dict[str, int]
    trace: dict[str, Any]
    skipped_refs: list[str] = field(default_factory=list)  # valid refs over MAX_ARTICLE_REFS


def chunk_from_row(row: Any, *, pinned: bool = False) -> ScoredChunk:
    """Build a `ScoredChunk` from a `sql.py` result row (score column optional)."""
    mapping = row._mapping
    return ScoredChunk(
        id=mapping["id"],
        chunk_type=mapping["chunk_type"],
        seq=mapping["seq"],
        article_no=mapping["article_no"],
        schedule_no=mapping["schedule_no"],
        appendix_no=mapping["appendix_no"],
        article_title=mapping["article_title"],
        text=mapping["text"],
        embed_text=mapping["embed_text"],
        score=float(mapping.get("score") or 0.0),
        pinned=pinned,
    )
