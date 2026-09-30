"""Expected-Article list from the PDF's Contents pages (spec: ingestion §3.11).

The Contents pages are parsed by this separate code path, reviewed by hand and committed as
`eval/fixtures/expected_articles.txt`; validation then checks the body segmenter against it.

Usage:
    python -m samvidhan.ingestion.contents data/raw/constitution.pdf \\
        > eval/fixtures/expected_articles.txt
"""

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from samvidhan.ingestion.extract import extract_lines
from samvidhan.ingestion.types import Line, strip_footnote_tokens

# The Contents write "243-I" / "243Z-O" (so I and O are not read as digits) and "371B ." ;
# the body prints 243I / 243ZO / 371B, which is the canonical form.
_CONTENTS_ENTRY = re.compile(r"^\s*(\[)?\s*(\d{1,3}(?:-?[A-Z]){0,3})\s*\.\s*(.*)$")
_OMITTED_MARK = "# omitted"


@dataclass(frozen=True, slots=True)
class ExpectedArticle:
    article_no: str
    is_omitted: bool


def parse_contents(lines: Sequence[Line], *, body_start_page: int) -> list[ExpectedArticle]:
    """Article entries listed before the body. A `[` prefix marks an omitted Article."""
    articles: list[ExpectedArticle] = []
    for line in lines:
        if line.page >= body_start_page:
            break
        match = _CONTENTS_ENTRY.match(strip_footnote_tokens(line.text))
        if match:
            omitted = bool(match.group(1)) or "Omitted" in match.group(3)
            articles.append(ExpectedArticle(match.group(2).replace("-", ""), omitted))
    return articles


def format_fixture(articles: Sequence[ExpectedArticle]) -> str:
    rows = [f"{a.article_no}  {_OMITTED_MARK}" if a.is_omitted else a.article_no for a in articles]
    return "\n".join(rows) + "\n"


def load_fixture(path: Path) -> list[ExpectedArticle]:
    """Read `expected_articles.txt`: one Article number per line, `# omitted` for omitted ones."""
    articles = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        number, _, comment = raw.partition("#")
        if number.strip():
            articles.append(ExpectedArticle(number.strip(), "omitted" in comment))
    return articles


def main(argv: Sequence[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    lines = extract_lines(Path(args[0]), min_text_page_ratio=0.0)
    body_page = next(line.page for line in lines if line.text.startswith("WE, THE PEOPLE"))
    sys.stdout.write(format_fixture(parse_contents(lines, body_start_page=body_page)))


if __name__ == "__main__":
    main()
