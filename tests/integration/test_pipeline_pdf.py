"""Full pipeline (no embeddings, no DB) on the real PDF (spec: ingestion §6). Needs the PDF and
the bge-m3 tokenizer (`make models`)."""

from pathlib import Path

import pytest
from huggingface_hub import try_to_load_from_cache

from samvidhan.core.config import Settings
from samvidhan.ingestion.chunk import ChunkRecord
from samvidhan.ingestion.contents import load_fixture
from samvidhan.ingestion.embed import TokenizerCounter
from samvidhan.ingestion.pipeline import PipelineResult, run_pipeline
from samvidhan.ingestion.types import FOOTNOTE_TOKEN_RE

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "data" / "raw" / "constitution.pdf"


@pytest.fixture(scope="module")
def result() -> PipelineResult:
    # Module-scoped, so the per-test DATABASE_URL fixture is not active yet; no DB is used here.
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x:x@localhost:1/x")  # type: ignore[call-arg,arg-type]
    if not PDF.exists():
        pytest.skip(f"{PDF} not present (gitignored)")
    if not isinstance(try_to_load_from_cache(settings.embed_model, "tokenizer.json"), str):
        pytest.skip("tokenizer not downloaded; run `make models`")
    return run_pipeline(
        PDF,
        settings=settings,
        expected=load_fixture(ROOT / "eval" / "fixtures" / "expected_articles.txt"),
        count_tokens=TokenizerCounter(settings.embed_model),
    )


def by_id(result: PipelineResult) -> dict[str, ChunkRecord]:
    return {chunk.id: chunk for chunk in result.chunks}


def test_validation_passes(result: PipelineResult) -> None:
    assert result.validation.ok, result.validation.as_dict()
    assert result.stubbed == ["238"]
    assert result.edition_date is not None and result.edition_date.isoformat() == "2024-05-01"
    assert all(chunk.token_count <= 1024 for chunk in result.chunks)


def test_spot_metadata(result: PipelineResult) -> None:
    chunks = by_id(result)
    art21 = chunks["art-21#0"]
    assert (art21.part_no, art21.group_heading) == ("III", "Right to Freedom")
    assert "art-21#1" not in chunks
    art21a = chunks["art-21A#0"]
    assert art21a.amendment_notes[0]["act"] == "Constitution (Eighty-sixth Amendment) Act"
    assert chunks["art-31#0"].is_omitted
    assert chunks["art-238#0"].is_omitted and chunks["art-238#0"].part_no == "VII"
    assert "art-368#1" in chunks and chunks["art-368#0"].clause_range
    assert chunks["art-368#1"].embed_text.startswith("Part XX — Amendment of the Constitution")
    assert len(chunks["preamble#0"].amendment_notes) == 2
    assert chunks["sch-7-list2#0"].text.startswith("1. Public order")
    assert chunks["app-2#0"].appendix_no == "II"
    assert "JAMMU AND KASHMIR" in (chunks["app-2#0"].part_title or "")


def test_appendix_i_does_not_leak_structure(result: PipelineResult) -> None:
    """Appendix I has its own "THE FIRST SCHEDULE" and "PA R T I"; they must stay appendix text."""
    first_appendix = min(c.seq for c in result.chunks if c.chunk_type == "appendix")
    tail = [c for c in result.chunks if c.seq >= first_appendix]
    assert {c.chunk_type for c in tail} == {"appendix"}
    assert not any(c.article_no or c.schedule_no for c in tail)


def test_no_footnote_tokens_left_in_text(result: PipelineResult) -> None:
    assert not any(FOOTNOTE_TOKEN_RE.search(c.text) or "{{" in c.embed_text for c in result.chunks)
