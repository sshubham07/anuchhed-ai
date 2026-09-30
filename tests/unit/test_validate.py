"""Validate step (spec: ingestion §3.11)."""

from dataclasses import replace

from samvidhan.ingestion.chunk import ChunkRecord
from samvidhan.ingestion.contents import ExpectedArticle
from samvidhan.ingestion.validate import size_summary, validate_chunks

BASE = ChunkRecord(
    id="",
    chunk_type="article",
    seq=0,
    part_no=None,
    part_title=None,
    chapter=None,
    group_heading=None,
    article_no=None,
    article_title=None,
    schedule_no=None,
    appendix_no=None,
    clause_range=None,
    is_omitted=False,
    amendment_notes=(),
    text="",
    embed_text="",
    token_count=100,
)


def corpus(*articles: ChunkRecord) -> list[ChunkRecord]:
    chunks = [replace(BASE, id="preamble#0", chunk_type="preamble")]
    chunks += articles
    chunks += [
        replace(BASE, id=f"sch-{n}#0", chunk_type="schedule", schedule_no=str(n))
        for n in range(1, 13)
    ]
    chunks += [
        replace(BASE, id=f"app-{n}#0", chunk_type="appendix", appendix_no=n)
        for n in ("I", "II", "III")
    ]
    return [replace(c, seq=i) for i, c in enumerate(chunks)]


def a(no: str, piece: int = 0, **kw: object) -> ChunkRecord:
    return replace(BASE, id=f"art-{no}#{piece}", article_no=no, **kw)  # type: ignore[arg-type]


EXPECTED = [
    ExpectedArticle("21", False),
    ExpectedArticle("21A", False),
    ExpectedArticle("31", True),
]


def test_complete_corpus_is_ok() -> None:
    report = validate_chunks(
        corpus(a("21"), a("21A"), a("21A", 1), a("31", is_omitted=True)), EXPECTED, max_tokens=1024
    )
    assert report.ok, report.as_dict()


def test_missing_duplicate_unexpected_and_oversized_fail() -> None:
    report = validate_chunks(
        corpus(a("21"), a("21"), a("99"), a("31", token_count=2000)), EXPECTED, max_tokens=1024
    )
    assert not report.ok
    assert (report.missing, report.duplicates, report.unexpected) == (["21A"], ["21"], ["99"])
    assert report.over_limit == ["art-31#0"]
    assert report.omitted_mismatch == ["31"]  # informational


def test_missing_schedule_appendix_or_preamble_fails() -> None:
    chunks = [
        c
        for c in corpus(a("21"), a("21A"), a("31", is_omitted=True))
        if c.id not in ("sch-7#0", "app-II#0", "preamble#0")
    ]
    report = validate_chunks(chunks, EXPECTED, max_tokens=1024)
    assert (report.missing_schedules, report.missing_appendices, report.preamble_chunks) == (
        ["7"],
        ["II"],
        0,
    )
    assert not report.ok


def test_size_summary() -> None:
    summary = size_summary(
        [a("21", token_count=50), a("368", token_count=650), a("368", 1, token_count=120)]
    )
    assert summary["n_chunks"] == 3
    assert summary["token_histogram"] == {"0-99": 1, "100-199": 1, "600-699": 1}
    assert summary["split_articles"] == ["368"]


def test_duplicate_chunk_ids_of_any_type_fail() -> None:
    chunks = corpus(a("21"), a("21A"), a("31", is_omitted=True))
    chunks.append(replace(chunks[-1], seq=len(chunks)))  # app-III#0 twice
    report = validate_chunks(chunks, EXPECTED, max_tokens=1024)
    assert report.duplicate_ids == ["app-III#0"]
    assert not report.ok
