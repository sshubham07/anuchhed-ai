"""Extract step on the real PDF (spec: ingestion §3.1). Needs data/raw/constitution.pdf."""

import re
from pathlib import Path

import pytest

from samvidhan.ingestion.extract import extract_lines
from samvidhan.ingestion.types import Line

pytestmark = pytest.mark.slow

PDF = Path(__file__).resolve().parents[2] / "data" / "raw" / "constitution.pdf"


@pytest.fixture(scope="module")
def lines() -> list[Line]:
    if not PDF.exists():
        pytest.skip(f"{PDF} not present (gitignored); see spec ingestion §3.1")
    return extract_lines(PDF, min_text_page_ratio=0.9)


def _on_page(lines: list[Line], page: int, needle: str) -> Line:
    return next(line for line in lines if line.page == page and needle in line.text)


def test_page_count_and_no_raw_braces(lines: list[Line]) -> None:
    assert max(line.page for line in lines) == 402
    # The {{fn:N}} token scheme relies on the PDF never containing braces itself.
    assert all("{" not in re.sub(r"\{\{fn:\d+\}\}", "", line.text) for line in lines)


def test_article_headings(lines: list[Line]) -> None:
    art21 = _on_page(lines, 42, "21. Protection of life")
    assert art21.bold_prefix == "21. Protection of life and personal liberty."
    art21a = _on_page(lines, 42, "21A.")
    assert art21a.text.startswith("{{fn:2}}[21A. Right to education.—")
    assert art21a.bold_prefix == "[21A. Right to education."


def test_markers_on_footnote_heavy_pages_are_tokens(lines: list[Line]) -> None:
    # Pages where 7.9 pt footnote text dominates; a per-page body size missed these markers.
    assert _on_page(lines, 88, "(4)*").text.startswith("{{fn:1}}(4)*")
    assert not any(re.match(r"^\d+[\[(]", line.text) for line in lines)


def test_rebuilt_rows(lines: list[Line]) -> None:
    kerala = _on_page(lines, 361, "Kerala Land Reforms (Amendment) Act, 1969")
    assert kerala.text == "{{fn:1}}[65. The Kerala Land Reforms (Amendment) Act, 1969"
    assert len(kerala.parts) == 1
    enclave = _on_page(lines, 391, "Nazirganja 41")
    assert enclave.parts == ("8.", "Nazirganja", "41", "Boda", "Haldibari", "58.32")


def test_appendix_heading(lines: list[Line]) -> None:
    heading = _on_page(lines, 401, "APPENDIX")
    assert heading.is_bold
