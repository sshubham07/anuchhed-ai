"""Builders for ingestion unit tests."""

from samvidhan.ingestion.types import Line

MARGIN = 162.0  # body text left edge
INDENT = 186.0  # first row of an Article / clause
CENTER = 260.0  # centered headings


def row(
    text: str,
    *,
    x0: float = MARGIN,
    page: int = 40,
    y: float = 300.0,
    bold: str = "",
    parts: tuple[str, ...] | None = None,
    size: float = 9.1,
) -> Line:
    """A Line; `bold=...` is the bold prefix (use the whole text for a fully bold row)."""
    return Line(
        page=page, text=text, size=size, bold_prefix=bold, y=y, x0=x0, parts=parts or (text,)
    )
