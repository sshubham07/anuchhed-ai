"""Builders for retrieval unit tests."""

from samvidhan.retrieval.types import ScoredChunk


def chunk(
    chunk_id: str,
    *,
    article_no: str | None = None,
    schedule_no: str | None = None,
    text: str = "",
    seq: int = 0,
    pinned: bool = False,
) -> ScoredChunk:
    return ScoredChunk(
        id=chunk_id,
        chunk_type="schedule" if schedule_no else "article",
        seq=seq,
        article_no=article_no,
        schedule_no=schedule_no,
        appendix_no=None,
        article_title=None,
        text=text or chunk_id,
        embed_text=text or chunk_id,
        pinned=pinned,
    )


def leg(stage: str, *chunks: ScoredChunk) -> list[ScoredChunk]:
    """A ranked leg: ranks 1..n, descending scores."""
    return [c.with_stage(stage, rank, 1.0 / rank) for rank, c in enumerate(chunks, start=1)]
