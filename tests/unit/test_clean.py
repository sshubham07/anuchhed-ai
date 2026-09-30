"""Clean step (spec: ingestion §3.1-3.2)."""

import pytest

from samvidhan.core.errors import InvalidSourceError
from samvidhan.ingestion.clean import body_start, clean_lines, is_page_furniture, normalize_text
from tests.unit.ingestion_helpers import CENTER, row


def test_running_title_context_line_and_page_numbers_are_furniture() -> None:
    assert is_page_furniture(row("11 THE CONSTITUTION OF  INDIA", y=180))
    assert is_page_furniture(row("THE CONSTITUTION OF INDIA", y=180))
    assert is_page_furniture(row("(Part III.—Fundamental Rights)", y=197))
    assert is_page_furniture(row("(Seventh Schedule)", y=193))
    assert is_page_furniture(row("(Appendix I)", y=193))
    assert is_page_furniture(row("310", y=180))
    assert is_page_furniture(row("4", y=624))


def test_body_rows_are_not_furniture() -> None:
    assert not is_page_furniture(row("(Article 246)", y=230))  # schedule line below the header
    assert not is_page_furniture(row("(Part III.—Fundamental Rights)", y=400))
    assert not is_page_furniture(row("21", y=400))
    assert not is_page_furniture(row("THE CONSTITUTION OF INDIA", y=400))


def test_body_starts_at_the_preamble_followed_by_its_text() -> None:
    lines = [
        row("PREAMBLE", page=4, bold="PREAMBLE"),  # Contents entry: no text after it
        row("PART I", page=4),
        row("THE CONSTITUTION OF INDIA", page=32, y=225, bold="THE CONSTITUTION OF INDIA"),
        row("PREAMBLE", page=32, x0=CENTER, bold="PREAMBLE"),
        row("WE, THE PEOPLE OF INDIA, having solemnly resolved", page=32),
    ]
    assert body_start(lines) == 3
    assert [line.text for line in clean_lines(lines)][:2] == [
        "PREAMBLE",
        "WE, THE PEOPLE OF INDIA, having solemnly resolved",
    ]


def test_missing_body_start_is_invalid_source() -> None:
    with pytest.raises(InvalidSourceError, match="Body start not found"):
        body_start([row("Some other document")])


def test_normalize_text() -> None:
    assert normalize_text("“the  State”  and ‘x’\n") == "\"the State\" and 'x'"
    assert normalize_text("Title.—body") == "Title.—body"
