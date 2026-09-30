"""Validate step: chunks vs the expected-Article list and size limits (spec: ingestion §3.11).

Ingestion fails (nothing is stored) on any missing, duplicated or unexpected Article, a missing
Preamble / Schedule / Appendix, or a chunk over the embedder's token limit.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from samvidhan.ingestion.chunk import ChunkRecord
from samvidhan.ingestion.contents import ExpectedArticle

_PIECE = re.compile(r"#\d+$")
_SCHEDULES = [str(n) for n in range(1, 13)]
_APPENDICES = ["I", "II", "III"]


@dataclass(slots=True)
class ValidationReport:
    missing: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)  # any chunk id twice (PK clash)
    unexpected: list[str] = field(default_factory=list)
    omitted_mismatch: list[str] = field(default_factory=list)  # informational only
    missing_schedules: list[str] = field(default_factory=list)
    missing_appendices: list[str] = field(default_factory=list)
    preamble_chunks: int = 0
    over_limit: list[str] = field(default_factory=list)
    seq_ordered: bool = True

    @property
    def ok(self) -> bool:
        return not (
            self.missing
            or self.duplicates
            or self.duplicate_ids
            or self.unexpected
            or self.missing_schedules
            or self.missing_appendices
            or self.over_limit
            or self.preamble_chunks != 1
            or not self.seq_ordered
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing": self.missing,
            "duplicates": self.duplicates,
            "duplicate_ids": self.duplicate_ids,
            "unexpected": self.unexpected,
            "omitted_mismatch": self.omitted_mismatch,
            "missing_schedules": self.missing_schedules,
            "missing_appendices": self.missing_appendices,
            "preamble_chunks": self.preamble_chunks,
            "over_limit": self.over_limit,
            "seq_ordered": self.seq_ordered,
        }


def validate_chunks(
    chunks: Sequence[ChunkRecord], expected: Sequence[ExpectedArticle], *, max_tokens: int
) -> ValidationReport:
    articles = [c for c in chunks if c.chunk_type == "article"]
    # One Article = one run of pieces art-N#0, #1…; a second #0 means it was segmented twice.
    first_pieces = Counter(c.article_no for c in articles if c.id.endswith("#0"))
    found = {c.article_no for c in articles}
    expected_numbers = [e.article_no for e in expected]
    omitted = {c.article_no for c in articles if c.is_omitted}
    return ValidationReport(
        missing=[n for n in expected_numbers if n not in found],
        duplicates=sorted(n for n, count in first_pieces.items() if n and count > 1),
        duplicate_ids=sorted(i for i, count in Counter(c.id for c in chunks).items() if count > 1),
        unexpected=sorted(n for n in found if n and n not in set(expected_numbers)),
        omitted_mismatch=sorted(
            e.article_no
            for e in expected
            if e.article_no in found and e.is_omitted != (e.article_no in omitted)
        ),
        missing_schedules=[n for n in _SCHEDULES if not any(c.schedule_no == n for c in chunks)],
        missing_appendices=[n for n in _APPENDICES if not any(c.appendix_no == n for c in chunks)],
        preamble_chunks=sum(c.chunk_type == "preamble" for c in chunks),
        over_limit=[c.id for c in chunks if c.token_count > max_tokens],
        seq_ordered=all(a.seq < b.seq for a, b in zip(chunks, chunks[1:], strict=False)),
    )


def size_summary(chunks: Sequence[ChunkRecord]) -> dict[str, Any]:
    """Counts per type and a token histogram (buckets of 100) for the ingestion report."""
    tokens = [c.token_count for c in chunks] or [0]
    histogram = Counter(t // 100 * 100 for t in tokens)
    return {
        "n_chunks": len(chunks),
        "by_type": dict(Counter(c.chunk_type for c in chunks)),
        "tokens_min": min(tokens),
        "tokens_avg": round(sum(tokens) / len(tokens)),
        "tokens_max": max(tokens),
        "token_histogram": {f"{k}-{k + 99}": histogram[k] for k in sorted(histogram)},
        "split_articles": sorted(
            {c.article_no for c in chunks if c.article_no and not c.id.endswith("#0")}
        ),
    }
