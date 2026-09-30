"""Extract step (spec: ingestion §3.1-3.3, §7): rows, bold prefix, footnote tokens, text gate."""

from pathlib import Path
from typing import Any

import pymupdf
import pytest

from samvidhan.core.errors import InvalidSourceError
from samvidhan.ingestion.extract import (
    check_text_layer,
    document_body_size,
    extract_lines,
    page_lines,
)
from samvidhan.ingestion.types import Line, strip_footnote_tokens

BODY = 9.1
BOLD = 20  # PyMuPDF flags: bold (16) + serif (4)
REGULAR = 4


def span(
    text: str, x0: float, x1: float, *, size: float = BODY, flags: int = REGULAR
) -> dict[str, Any]:
    return {"text": text, "size": size, "flags": flags, "bbox": (x0, 0.0, x1, 0.0)}


def line(y0: float, *spans: dict[str, Any], height: float = 10.1) -> dict[str, Any]:
    return {"bbox": (0.0, y0, 0.0, y0 + height), "spans": list(spans)}


def page(*lines: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocks": [{"lines": [ln]} for ln in lines]
    }  # one block per line, like PyMuPDF often does


def rows(*lines: dict[str, Any]) -> list[Line]:
    return page_lines(page(*lines), 42, body_size=BODY)


def test_article_heading_has_bold_prefix_and_body() -> None:
    [row] = rows(
        line(
            447.1,
            span("21. Protection of life and personal liberty.", 186.0, 364.5, flags=BOLD),
            span("—No person shall be ", 364.3, 452.3),
        )
    )
    assert row.text == "21. Protection of life and personal liberty.—No person shall be"
    assert row.bold_prefix == "21. Protection of life and personal liberty."
    assert not row.is_bold
    assert (row.page, row.x0, row.size) == (42, 186.0, BODY)


def test_footnote_number_becomes_token_and_is_skipped_by_bold_prefix() -> None:
    [row] = rows(
        line(
            481.9,
            span("2", 186.0, 189.0, size=6.0),
            span("[21A. Right to education.", 189.1, 300.8, flags=BOLD),
            span("—The State shall provide free and ", 301.0, 452.3),
        )
    )
    assert row.text == "{{fn:2}}[21A. Right to education.—The State shall provide free and"
    assert row.bold_prefix == "[21A. Right to education."
    assert row.size == BODY  # the 6 pt marker does not count


def test_small_digits_after_text_are_tokens_but_footnote_sized_digits_are_not() -> None:
    [body, footnote] = rows(
        line(305.0, span("India into a ", 162.0, 220.0), span("1", 220.1, 223.0, size=6.0)),
        line(582.0, span("1. Subs. by the Constitution", 162.0, 300.0, size=7.9), height=8.7),
    )
    assert body.text == "India into a {{fn:1}}"
    assert footnote.text == "1. Subs. by the Constitution"


def test_header_and_page_number_on_one_row_are_merged_left_to_right() -> None:
    [header, context] = rows(
        line(180.3, span("THE CONSTITUTION OF  INDIA", 237.4, 374.7, flags=BOLD)),
        line(180.3, span("11", 162.0, 171.1)),
        line(196.7, span("(Part III.—Fundamental Rights)", 241.7, 370.5, size=10.1)),
    )
    assert header.text == "11 THE CONSTITUTION OF  INDIA"
    assert header.parts == ("11", "THE CONSTITUTION OF  INDIA")
    assert context.text == "(Part III.—Fundamental Rights)"


def test_justified_words_stay_one_part_but_table_cells_split() -> None:
    table, justified = rows(  # returned top to bottom: the table row has the smaller y
        line(
            451.9,
            span("66. ", 176.4, 189.3),
            span("The ", 201.6, 216.9),
            span("Kerala ", 229.2, 254.1),
        ),
        line(
            232.6,
            span("8. ", 162.0, 171.0),
            span("Nazirganja ", 191.3, 233.0),
            span("41", 277.4, 286.6),
        ),
    )
    assert justified.parts == ("66. The Kerala",)
    assert table.parts == ("8.", "Nazirganja", "41")
    assert table.text == "8. Nazirganja 41"


def test_spans_without_whitespace_get_a_space_only_across_a_gap() -> None:
    [row] = rows(
        line(
            216.3,
            span("(6) Nothing in sub-clause (", 186.0, 284.3),
            span("g", 284.4, 289.0, flags=6),  # italic, touching
            span(") of the said clause", 288.7, 400.0),
            span("applies", 403.0, 430.0),  # 3 pt gap, no whitespace
        )
    )
    assert row.text == "(6) Nothing in sub-clause (g) of the said clause applies"


def test_fully_bold_row_is_bold() -> None:
    [part, title] = rows(
        line(216.0, span("PART II", 287.3, 320.0, flags=BOLD)),
        line(229.0, span("CITIZENSHIP ", 278.6, 330.0)),
    )
    assert part.is_bold and part.bold_prefix == "PART II"
    assert not title.is_bold and title.bold_prefix == ""


def test_rows_are_ordered_top_to_bottom_regardless_of_block_order() -> None:
    result = rows(
        line(300.0, span("second", 162.0, 200.0)),
        line(200.0, span("first", 162.0, 200.0)),
    )
    assert [row.text for row in result] == ["first", "second"]


def test_whitespace_only_spans_and_empty_lines_are_dropped() -> None:
    result = rows(
        line(193.1, span("(Appendix I)", 282.5, 329.5), span(" ", 329.3, 332.3, size=12.0)),
        line(210.0, span("   ", 162.0, 170.0)),
    )
    assert [row.text for row in result] == ["(Appendix I)"]


def test_document_body_size_is_the_most_used_size() -> None:
    pages = [
        page(line(0, span("x" * 50, 0, 1)), line(20, span("y" * 60, 0, 1, size=7.9))),
        page(line(0, span("z" * 40, 0, 1))),
    ]
    assert document_body_size(pages) == BODY


def test_text_layer_gate() -> None:
    text_page = page(line(0, span("Some article text on this page", 0, 1)))
    blank = page()
    check_text_layer([text_page] * 9 + [blank], min_text_page_ratio=0.9)
    with pytest.raises(InvalidSourceError, match="2/3 pages"):
        check_text_layer([text_page, text_page, blank], min_text_page_ratio=0.9)
    with pytest.raises(InvalidSourceError):
        check_text_layer([], min_text_page_ratio=0.9)


def test_extract_lines_from_a_generated_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "tiny.pdf"
    with pymupdf.open() as doc:
        for number in (1, 2):
            pg = doc.new_page()
            pg.insert_text((72, 100), f"PART {number}", fontname="hebo", fontsize=BODY)
            pg.insert_text((72, 120), "21. Protection of life.", fontname="hebo", fontsize=BODY)
            pg.insert_text(
                (181, 120), "-No person shall be deprived", fontname="helv", fontsize=BODY
            )
            pg.insert_text(
                (72, 140), "of his life or personal liberty", fontname="helv", fontsize=BODY
            )
            pg.insert_text((72, 157), "2", fontname="helv", fontsize=6.0)
            pg.insert_text((75, 160), "[Inserted text.]", fontname="helv", fontsize=BODY)
        doc.save(pdf)

    lines = extract_lines(pdf, min_text_page_ratio=0.9)

    assert [ln.page for ln in lines] == [1] * 4 + [2] * 4
    part, heading, _, inserted = lines[:4]
    assert part.is_bold and part.text == "PART 1"
    assert heading.bold_prefix == "21. Protection of life."
    assert heading.text.startswith("21. Protection of life.")
    assert inserted.text == "{{fn:2}}[Inserted text.]"
    assert strip_footnote_tokens(inserted.text) == "[Inserted text.]"


def test_extract_lines_rejects_a_non_pdf(tmp_path: Path) -> None:
    bogus = tmp_path / "not.pdf"
    bogus.write_text("hello")
    with pytest.raises(InvalidSourceError, match="Cannot open PDF"):
        extract_lines(bogus, min_text_page_ratio=0.9)
    with pytest.raises(InvalidSourceError):
        extract_lines(tmp_path / "missing.pdf", min_text_page_ratio=0.9)
