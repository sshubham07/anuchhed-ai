"""Footnotes step: split pages at the footnote rule, parse amendment notes (spec: ingestion §3.2).

Footnotes on a page are numbered 1..K in order, so the k-th footnote start is footnote k even
where the printed number is a typo (p. 107 prints "2." twice). A row starts a footnote when it
begins with a number plausible as the next one (≤ previous + 2); this skips continuation rows
that begin with a year ("1960 …"). Rows before the first numbered footnote are an unnumbered
editorial note (n = 0), e.g. the Sambamurthy note on p. 271.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from samvidhan.ingestion.clean import normalize_text
from samvidhan.ingestion.types import FOOTNOTE_TOKEN_RE, Line

EDITORIAL_NOTE = 0  # footnote number for an unnumbered note at the top of a footnote area

_RULE = re.compile(r"^_{5,}$")
_FOOTNOTE_START = re.compile(r"^(\d{1,2})(\.\s*|\s+)(?=\S)")
_ACTION = re.compile(
    r"\b(Ins|Subs|Omitted|omitted|Added|Rep|Renumbered|renumbered|Re-numbered|Deleted)\b\.?"
)
_ACT = re.compile(r"\b((?:the )?[A-Z][\w\s,&()'-]*?(?:Act|Order)),?\s*(\d{4})")


@dataclass(frozen=True, slots=True)
class Footnote:
    page: int
    n: int
    text: str

    def as_note(self) -> dict[str, Any]:
        """The `amendment_notes` jsonb item: verbatim text + best-effort action/act/year."""
        action = _ACTION.search(self.text)
        act = _ACT.search(self.text)
        return {
            "n": self.n,
            "text": self.text,
            "action": action.group(1).capitalize() if action else None,
            "act": act.group(1).removeprefix("the ").strip() if act else None,
            "year": int(act.group(2)) if act else None,
        }


FootnoteIndex = dict[tuple[int, int], Footnote]  # (page, n) → footnote


def split_footnotes(lines: Sequence[Line]) -> tuple[list[Line], FootnoteIndex]:
    """Body rows (footnote areas removed) and every footnote keyed by (page, n)."""
    body: list[Line] = []
    footnotes: FootnoteIndex = {}
    for page, page_rows in groupby(lines, key=lambda line: line.page):
        rows = list(page_rows)
        rule = next((i for i, row in enumerate(rows) if _RULE.match(row.text.strip())), None)
        if rule is None:
            body.extend(rows)
            continue
        body.extend(rows[:rule])
        referenced = {
            int(token.group(1))
            for row in rows[:rule]
            for token in FOOTNOTE_TOKEN_RE.finditer(row.text)
        }
        for footnote in parse_footnote_area(page, rows[rule + 1 :], referenced=referenced):
            footnotes[(page, footnote.n)] = footnote
    return body, footnotes


def parse_footnote_area(
    page: int, rows: Sequence[Line], *, referenced: set[int] | None = None
) -> list[Footnote]:
    """Footnotes below one page's rule, numbered by position.

    "N." always starts a footnote; "N " without the dot (p. 42 prints "2 Ins. by…") only when
    the page body references N, so a continuation row like "1 S.C.C. 362." is not a footnote.
    """
    groups: list[list[str]] = []
    note: list[str] = []
    last_printed = 0
    for row in rows:
        text = row.text.strip()
        start = _FOOTNOTE_START.match(text)
        if start and _is_start(int(start.group(1)), start.group(2), last_printed, referenced):
            last_printed = max(int(start.group(1)), last_printed + 1)
            groups.append([text[start.end() :]])
        elif groups:
            groups[-1].append(text)
        else:
            note.append(text)
    footnotes = [Footnote(page, k, join_rows(group)) for k, group in enumerate(groups, start=1)]
    if note:
        footnotes.insert(0, Footnote(page, EDITORIAL_NOTE, join_rows(note)))
    return footnotes


def _is_start(number: int, separator: str, last: int, referenced: set[int] | None) -> bool:
    if not 1 <= number <= last + 2:
        return False
    return "." in separator or (referenced is not None and number in referenced)


def join_rows(rows: Sequence[str]) -> str:
    """Join wrapped rows: a row ending in "-" is glued (`inter-` + `State`), others get a space."""
    text = ""
    for row in rows:
        row = row.strip()
        if not row:
            continue
        text += row if text.endswith("-") or not text else " " + row
    return normalize_text(text)
