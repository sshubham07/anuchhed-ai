"""The ingestion pipeline without I/O side effects (spec: ingestion §3.2).

extract → clean → footnotes → segment (+ stubs for omitted Articles with no text) → chunk →
validate. Embedding and storage happen in the CLI, so this runs in seconds and is testable.
"""

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from samvidhan.core.config import Settings
from samvidhan.core.logging import get_logger
from samvidhan.ingestion.chunk import ChunkConfig, ChunkRecord, TokenCounter, build_chunks
from samvidhan.ingestion.clean import clean_lines
from samvidhan.ingestion.contents import ExpectedArticle
from samvidhan.ingestion.extract import extract_lines
from samvidhan.ingestion.footnotes import split_footnotes
from samvidhan.ingestion.segment import add_omitted_stubs, segment_lines
from samvidhan.ingestion.types import Line
from samvidhan.ingestion.validate import ValidationReport, size_summary, validate_chunks

_EDITION = re.compile(r"As on (\d{1,2})(?:st|nd|rd|th)?\s+(\w+),?\s+(\d{4})")

log = get_logger(__name__)


@dataclass(slots=True)
class PipelineResult:
    chunks: list[ChunkRecord]
    validation: ValidationReport
    edition_date: date | None
    orphan_markers: list[tuple[int, int]]
    stubbed: list[str]
    timings_ms: dict[str, int] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        return {
            "validation": self.validation.as_dict(),
            **size_summary(self.chunks),
            "omitted_articles": sorted(
                {c.article_no for c in self.chunks if c.is_omitted and c.article_no}
            ),
            "stubbed_from_contents": self.stubbed,
            "orphan_footnote_markers": [list(key) for key in self.orphan_markers],
            "edition_date": self.edition_date.isoformat() if self.edition_date else None,
            "timings_ms": self.timings_ms,
        }


def chunk_config(settings: Settings) -> ChunkConfig:
    return ChunkConfig(
        max_tokens=settings.chunk_max_tokens,
        target_min=settings.chunk_target_min_tokens,
        target_max=settings.chunk_target_max_tokens,
        schedule7_entries=settings.schedule7_entries_per_chunk,
    )


def run_pipeline(
    pdf: Path,
    *,
    settings: Settings,
    expected: Sequence[ExpectedArticle],
    count_tokens: TokenCounter,
) -> PipelineResult:
    timings: dict[str, int] = {}
    lines = _timed(
        "extract",
        timings,
        lambda: extract_lines(pdf, min_text_page_ratio=settings.ingest_min_text_page_ratio),
    )
    body = _timed("clean", timings, lambda: clean_lines(lines))
    body, footnotes = _timed("footnotes", timings, lambda: split_footnotes(body))
    result = _timed("segment", timings, lambda: segment_lines(body, footnotes))
    found = {s.article_no for s in result.segments}
    stubbed = [e.article_no for e in expected if e.is_omitted and e.article_no not in found]
    segments = add_omitted_stubs(result, stubbed)
    chunks = _timed(
        "chunk", timings, lambda: build_chunks(segments, count_tokens, chunk_config(settings))
    )
    validation = _timed(
        "validate",
        timings,
        lambda: validate_chunks(chunks, expected, max_tokens=settings.embed_max_length),
    )
    return PipelineResult(
        chunks=chunks,
        validation=validation,
        edition_date=edition_date(lines),
        orphan_markers=result.orphan_markers,
        stubbed=[s for s in stubbed if any(c.article_no == s for c in chunks)],
        timings_ms=timings,
    )


def edition_date(lines: Sequence[Line]) -> date | None:
    """The "[As on 1st May, 2024]" date on the title page, if present."""
    for line in lines[:40]:
        match = _EDITION.search(line.text)
        if match:
            day, month, year = match.groups()
            try:
                return datetime.strptime(f"{day} {month} {year}", "%d %B %Y").date()
            except ValueError:  # "Sept", "Jan." …: let the caller ask for --version-date
                log.warning("edition_date_unparsed", text=match.group(0))
                return None
    return None


def _timed[T](stage: str, timings: dict[str, int], step: Callable[[], T]) -> T:
    start = time.perf_counter()
    value = step()
    timings[stage] = round((time.perf_counter() - start) * 1000)
    count = len(value) if isinstance(value, list) else None
    log.info("ingestion_stage_completed", stage=stage, count=count, duration_ms=timings[stage])
    return value
