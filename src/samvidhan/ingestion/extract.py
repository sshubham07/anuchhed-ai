"""Extract step: PDF → rows with size, bold prefix and footnote tokens (spec: ingestion §3.1-3.3).

PyMuPDF gives blocks → lines → spans, but its lines are not always visual rows: the running header
and the page number are separate blocks on one row, and some pages emit one word per span. So rows
are rebuilt from PyMuPDF lines whose vertical extents overlap, and spans are ordered left to right.

Footnote numbers are small raised digit spans (≈6 pt on 9.1 pt body text). They are written into the
row text as `{{fn:N}}` tokens, so later steps never confuse them with real digits.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from samvidhan.core.errors import InvalidSourceError
from samvidhan.ingestion.types import Line, footnote_token

_BOLD_FLAG = 1 << 4  # PyMuPDF span flag
_SUPERSCRIPT_SIZE_RATIO = 0.75  # digit spans smaller than this × body size are footnote numbers
# A wider horizontal gap separates table cells (justified word gaps are ≤ ~12 pt).
_CELL_GAP_PT = 15.0
_WORD_GAP_PT = 1.0  # a wider gap between spans with no whitespace gets a space
_ROW_OVERLAP_RATIO = 0.5  # lines overlapping by this share of the shorter height are one row
_MIN_PAGE_TEXT_CHARS = 20  # a page with fewer extracted characters counts as having no text layer


@dataclass(frozen=True, slots=True)
class _Span:
    text: str
    size: float
    bold: bool
    x0: float
    x1: float


@dataclass(frozen=True, slots=True)
class _PdfLine:
    y0: float
    y1: float
    spans: tuple[_Span, ...]


def extract_lines(pdf_path: Path, *, min_text_page_ratio: float) -> list[Line]:
    """All rows of the PDF in reading order.

    Raises `InvalidSourceError` for an unreadable or scanned PDF.
    """
    try:
        with pymupdf.open(pdf_path) as doc:
            pages: list[dict[str, Any]] = [page.get_text("dict") for page in doc]
    except RuntimeError as exc:  # every PyMuPDF open error (missing, empty, corrupt) subclasses it
        raise InvalidSourceError(f"Cannot open PDF {pdf_path}: {exc}") from exc
    check_text_layer(pages, min_text_page_ratio=min_text_page_ratio)
    body_size = document_body_size(pages)
    return [
        line
        for number, page in enumerate(pages, start=1)
        for line in page_lines(page, number, body_size=body_size)
    ]


def check_text_layer(pages: Sequence[dict[str, Any]], *, min_text_page_ratio: float) -> None:
    """Fail fast on scanned PDFs: at least `min_text_page_ratio` of pages must yield text."""
    with_text = sum(1 for page in pages if _char_count(page) >= _MIN_PAGE_TEXT_CHARS)
    if not pages or with_text / len(pages) < min_text_page_ratio:
        raise InvalidSourceError(
            f"Only {with_text}/{len(pages)} pages have a text layer "
            f"(need {min_text_page_ratio:.0%}); scanned PDFs are not supported"
        )


def document_body_size(pages: Sequence[dict[str, Any]]) -> float:
    """The font size carrying the most characters in the whole document (the body text size).

    Document-wide, not per page: on footnote-heavy pages the 7.9 pt footnote text dominates, which
    would push 6 pt footnote numbers over the superscript threshold.
    """
    return _body_size(span for page in pages for line in _pdf_lines(page) for span in line.spans)


def page_lines(page: dict[str, Any], page_number: int, *, body_size: float) -> list[Line]:
    """Rows of one page (a PyMuPDF `get_text("dict")` result), top to bottom."""
    rows = _group_rows(list(_pdf_lines(page)))
    return [_build_line(row, page_number, body_size) for row in rows]


def _pdf_lines(page: dict[str, Any]) -> Iterable[_PdfLine]:
    for block in page.get("blocks", []):
        for line in block.get("lines", []):
            spans = tuple(
                _Span(
                    text=span["text"],
                    size=float(span["size"]),
                    bold=bool(span["flags"] & _BOLD_FLAG),
                    x0=float(span["bbox"][0]),
                    x1=float(span["bbox"][2]),
                )
                for span in line["spans"]
                if span["text"].strip()
            )
            if spans:
                yield _PdfLine(y0=float(line["bbox"][1]), y1=float(line["bbox"][3]), spans=spans)


def _group_rows(lines: list[_PdfLine]) -> list[list[_PdfLine]]:
    rows: list[list[_PdfLine]] = []
    for line in sorted(lines, key=lambda item: (item.y0, item.y1)):
        if rows and _overlaps(rows[-1], line):
            rows[-1].append(line)
        else:
            rows.append([line])
    return rows


def _overlaps(row: list[_PdfLine], line: _PdfLine) -> bool:
    top, bottom = min(item.y0 for item in row), max(item.y1 for item in row)
    overlap = min(bottom, line.y1) - max(top, line.y0)
    shorter = min(bottom - top, line.y1 - line.y0)
    return shorter > 0 and overlap >= _ROW_OVERLAP_RATIO * shorter


def _body_size(spans: Iterable[_Span]) -> float:
    """The font size carrying the most characters on the page."""
    weight: dict[float, int] = {}
    for span in spans:
        size = round(span.size, 1)
        weight[size] = weight.get(size, 0) + len(span.text.strip())
    return max(weight, key=lambda size: weight[size]) if weight else 0.0


def _is_footnote_number(span: _Span, body_size: float) -> bool:
    return span.text.strip().isdigit() and span.size < _SUPERSCRIPT_SIZE_RATIO * body_size


def _build_line(row: list[_PdfLine], page_number: int, body_size: float) -> Line:
    spans = sorted((span for line in row for span in line.spans), key=lambda span: span.x0)
    parts: list[str] = [""]
    prefix = ""
    in_prefix = True
    previous: _Span | None = None
    for span in spans:
        is_marker = _is_footnote_number(span, body_size)
        text = footnote_token(int(span.text.strip())) if is_marker else span.text
        separator = _separator(previous, parts[-1], span, text)
        if separator == "|":
            parts.append("")
        else:
            parts[-1] += separator
        parts[-1] += text
        if not is_marker:
            in_prefix = in_prefix and span.bold
            if in_prefix:
                prefix += (" " if separator else "") + span.text
        previous = span
    return Line(
        page=page_number,
        text=" ".join(part.strip() for part in parts),
        size=_dominant_size(spans, body_size),
        bold_prefix=" ".join(prefix.split()),
        y=min(line.y0 for line in row),
        x0=spans[0].x0,
        parts=tuple(part.strip() for part in parts),
    )


def _separator(previous: _Span | None, current_part: str, span: _Span, text: str) -> str:
    """How `span` joins the row: "|" = new cell, " " = word space, "" = glued.

    Glued is right for a footnote token and its bracket: `{{fn:2}}` + `[21A.`.
    """
    if previous is None:
        return ""
    gap = span.x0 - previous.x1
    if gap > _CELL_GAP_PT:
        return "|"
    has_space = current_part[-1:].isspace() or text[:1].isspace()
    return " " if gap > _WORD_GAP_PT and not has_space else ""


def _dominant_size(spans: list[_Span], body_size: float) -> float:
    text_spans = [span for span in spans if not _is_footnote_number(span, body_size)] or spans
    return _body_size(text_spans)


def _char_count(page: dict[str, Any]) -> int:
    return sum(len(span.text.strip()) for line in _pdf_lines(page) for span in line.spans)
