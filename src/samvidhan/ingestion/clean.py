"""Clean step: keep the body, drop running headers and page numbers (spec: ingestion §3.1-3.2).

The body starts at the bold `PREAMBLE` heading followed by "WE, THE PEOPLE" (the Contents pages
list `PREAMBLE` too) and runs to the end of the document (Appendices included).
"""

import re
from collections.abc import Sequence

from samvidhan.core.errors import InvalidSourceError
from samvidhan.ingestion.types import Line

_HEADER_ZONE_Y = (
    212.0  # running header rows sit at y≈180 (title/page number) and y≈193-197 (context)
)
_FOOTER_ZONE_Y = 600.0  # a lone page number at the bottom of chapter-start pages
_RUNNING_TITLE = re.compile(r"^(\d+\s+)?THE CONSTITUTION OF\s+INDIA(\s+\d+)?$")
_CONTEXT_LINE = re.compile(r"^\((Part [^)]*|[A-Z][a-z]+ Schedule|Appendix [IVX]+|Preamble)\)$")
_PAGE_NUMBER = re.compile(r"^\d{1,3}$")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def clean_lines(lines: Sequence[Line]) -> list[Line]:
    """Body rows: Preamble heading to the end, without running headers and page numbers."""
    start = body_start(lines)
    return [line for line in lines[start:] if not is_page_furniture(line)]


def body_start(lines: Sequence[Line]) -> int:
    """Index of the `PREAMBLE` heading that opens the body; `InvalidSourceError` if absent."""
    for index, line in enumerate(lines):
        if line.is_bold and line.text.strip() == "PREAMBLE" and _followed_by_preamble(lines, index):
            return index
    raise InvalidSourceError("Body start not found: no bold PREAMBLE heading followed by its text")


def is_page_furniture(line: Line) -> bool:
    """Running title, context line (`(Part III.—Fundamental Rights)`) or a lone page number."""
    text = " ".join(line.text.split())
    if line.y < _HEADER_ZONE_Y and (_RUNNING_TITLE.match(text) or _CONTEXT_LINE.match(text)):
        return True
    return bool(_PAGE_NUMBER.match(text)) and (line.y < _HEADER_ZONE_Y or line.y > _FOOTER_ZONE_Y)


def normalize_text(text: str) -> str:
    """Straight quotes and single spaces; dashes are kept (`Title.—` separates heading and body)."""
    return " ".join(text.translate(_QUOTES).split())


def _followed_by_preamble(lines: Sequence[Line], index: int) -> bool:
    following = lines[index + 1 : index + 4]
    return any(line.text.lstrip().startswith("WE, THE PEOPLE") for line in following)
