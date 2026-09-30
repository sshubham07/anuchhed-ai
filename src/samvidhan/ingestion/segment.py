"""Segment step: body rows → Preamble / Article / Schedule / Appendix segments (spec §3.4).

Layout facts this relies on (this edition):
- Part titles, Chapter lines and group headings ("Right to Freedom") are centered, non-bold rows.
- An Article heading row starts at the heading indent (x0 ≈ 186) as `21. Title.—body`. Bold is
  not reliable (omitted Articles are often not bold), so the text pattern and indent decide, and
  Article numbers never go back (a guard against numbered list items).
- Inside a Schedule only Schedule / List / Appendix headings are structure; inside an Appendix
  only Appendix headings are (the amending Act's own "THE FIRST SCHEDULE" stays body text).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from samvidhan.ingestion.footnotes import EDITORIAL_NOTE, Footnote, FootnoteIndex
from samvidhan.ingestion.types import FOOTNOTE_TOKEN_RE, Line, strip_footnote_tokens

SegmentKind = Literal["preamble", "article", "schedule", "appendix"]
_Mode = Literal["start", "preamble", "parts", "schedule", "appendix"]
_Heading = Literal["part", "chapter", "group"]

_CENTER_X0 = 215.0  # centered headings start right of this; body rows start at 162-200
_HEADING_INDENT = (175.0, 196.0)  # x0 range of an Article heading row (Art. 95 is at 179.9)
_MAX_TITLE_ROWS = 3  # an Article title wraps over at most this many rows

_ORDINALS = "FIRST SECOND THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH ELEVENTH TWELFTH"
_PART = re.compile(r"^PART\s+([IVXL]+[A-Z]?)$")
_CHAPTER = re.compile(r"^CHAPTER\s+([IVXL]+)\.?\s*[—-]*\s*(.*)$")
# "371-I." is printed with a hyphen (so I is not read as 1); the canonical number is 371I.
_ARTICLE = re.compile(r"^(\d{1,3})((?:-?[A-Z]){0,3})\.\s*(?=[A-Z\[])")
_SCHEDULE = re.compile(rf"^({'|'.join(_ORDINALS.split())})\s+SCHEDULE$")
_LIST = re.compile(r"^List\s+(I{1,3})\s*[—-]")
_APPENDIX = re.compile(r"^APPENDIX\s+(I{1,3})$")
_APPENDIX_TITLE_END = re.compile(r"^(C\.O\.|\[)")
_TITLE_PREFIX = re.compile(r"^[\s\[*]*\d{1,3}(?:-?[A-Z]){0,3}\.\s*")
_OMITTED_BODY = re.compile(r"^[\s\]\[.—-]*(Omitted|Rep\.|Repealed)\b|^[\s*\]\[.]*$")
_BRACKETS = re.compile(r"[\[\]*]")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


@dataclass(frozen=True, slots=True)
class Segment:
    kind: SegmentKind
    seq: int
    lines: tuple[Line, ...]  # body rows; an Article's first row starts after its title
    footnotes: tuple[Footnote, ...] = ()
    part_no: str | None = None
    part_title: str | None = None  # for an Appendix: its title
    chapter: str | None = None
    group_heading: str | None = None
    article_no: str | None = None
    article_title: str | None = None
    schedule_no: str | None = None
    schedule_list: str | None = None  # "I" / "II" / "III" in the Seventh Schedule
    appendix_no: str | None = None
    is_omitted: bool = False


@dataclass(frozen=True, slots=True)
class EmptyPart:
    """A Part heading with no Article under it (Part VII was omitted as a whole)."""

    part_no: str
    part_title: str | None
    footnotes: tuple[Footnote, ...]
    after_seq: int  # seq of the segment before it


@dataclass(slots=True)
class SegmentResult:
    segments: list[Segment]
    empty_parts: list[EmptyPart]
    orphan_markers: list[tuple[int, int]]  # (page, n) tokens with no footnote on that page


@dataclass(slots=True)
class _Draft:
    kind: SegmentKind
    rows: list[Line] = field(default_factory=list)
    part_no: str | None = None
    part_title: str | None = None
    chapter: str | None = None
    group_heading: str | None = None
    article_no: str | None = None
    schedule_no: str | None = None
    schedule_list: str | None = None
    appendix_no: str | None = None


@dataclass(slots=True)
class _EmptyDraft:
    part_no: str
    part_title: str | None
    rows: list[Line]
    after: int


@dataclass(slots=True)
class _State:
    mode: _Mode = "start"
    part_no: str | None = None
    part_title: str | None = None
    part_rows: list[Line] = field(default_factory=list)
    part_has_articles: bool = False
    chapter: str | None = None
    group_heading: str | None = None
    schedule_no: str | None = None
    last_article: int = 0
    last_heading: _Heading | None = None  # set while heading rows are stacking up
    draft: _Draft | None = None
    done: list[_Draft] = field(default_factory=list)
    empty: list[_EmptyDraft] = field(default_factory=list)

    def open(self, draft: _Draft) -> None:
        self.close()
        self.draft = draft

    def close(self) -> None:
        if self.draft is not None:
            self.done.append(self.draft)
        self.draft = None

    def add(self, line: Line) -> None:
        if self.draft is not None:
            self.draft.rows.append(line)


def plain(line: Line) -> str:
    """Row text without footnote tokens or a leading `[`/`*`, with single spaces."""
    text = " ".join(strip_footnote_tokens(line.text).split())
    return text.lstrip("[*").strip()


def segment_lines(lines: Sequence[Line], footnotes: FootnoteIndex) -> SegmentResult:
    """Walk the body rows once and cut them into segments (spec §3.4)."""
    state = _State()
    for index, line in enumerate(lines):
        _step(state, line, lines, index)
    _end_part(state)
    state.close()
    return _finish(state, footnotes)


def _step(state: _State, line: Line, lines: Sequence[Line], index: int) -> None:
    text = plain(line)
    if match := _APPENDIX.match(text):
        _end_part(state)
        _reset(state, "appendix")
        title = _appendix_title(lines, index)
        state.open(_Draft("appendix", appendix_no=match.group(1), part_title=title))
        return
    if state.mode == "appendix":
        if not _is_appendix_title_row(state, text):
            state.add(line)
        return
    if _is_centered(line) and (match := _SCHEDULE.match(text)):
        _end_part(state)
        _reset(state, "schedule")
        state.schedule_no = str(_ORDINALS.split().index(match.group(1)) + 1)
        state.open(_Draft("schedule", schedule_no=state.schedule_no))
        return
    if state.mode == "schedule":
        _schedule_row(state, line, text)
    elif state.mode == "start":
        if line.is_bold and text == "PREAMBLE":
            state.open(_Draft("preamble"))
            state.mode = "preamble"
    else:
        _parts_row(state, line, text)


def _parts_row(state: _State, line: Line, text: str) -> None:
    if _is_centered(line) and (match := _PART.match(text)):
        _end_part(state)
        state.close()
        state.mode = "parts"
        state.part_no, state.part_title, state.chapter, state.group_heading = (
            match.group(1),
            None,
            None,
            None,
        )
        state.part_rows, state.part_has_articles, state.last_heading = [line], False, "part"
    elif state.mode == "preamble":
        state.add(line)
    elif _is_heading_row(state, line, text):
        _heading_row(state, line, text)
    elif (article := _article_heading(state, line, text)) is not None:
        state.last_article, state.part_has_articles, state.last_heading = article[0], True, None
        state.open(
            _Draft(
                "article",
                rows=[line],
                article_no=f"{article[0]}{article[1]}",
                part_no=state.part_no,
                part_title=state.part_title,
                chapter=state.chapter,
                group_heading=state.group_heading,
            )
        )
    else:
        state.last_heading = None
        state.add(line)


def _heading_row(state: _State, line: Line, text: str) -> None:
    caps = text.upper() == text
    if match := _CHAPTER.match(text):
        roman, title = match.group(1), _title_case(match.group(2))
        state.chapter = f"Chapter {roman} — {title}" if title else f"Chapter {roman}"
        state.group_heading, state.last_heading = None, "chapter"
    elif state.last_heading == "part" and (state.part_title is None or caps):
        state.part_title = _join(state.part_title, _title_case(_BRACKETS.sub("", text).strip(". ")))
        state.part_rows.append(line)
    elif state.last_heading == "chapter" and caps:
        state.chapter = _join(state.chapter, _title_case(text))
    elif state.last_heading == "group":
        state.group_heading = _join(state.group_heading, text.strip("[]"))
    else:
        state.group_heading, state.last_heading = text.strip("[]"), "group"


def _schedule_row(state: _State, line: Line, text: str) -> None:
    if state.schedule_no == "7" and (match := _LIST.match(text)):
        state.open(
            _Draft("schedule", schedule_no="7", schedule_list=match.group(1), group_heading=text)
        )
    else:
        state.add(line)


def _article_heading(state: _State, line: Line, text: str) -> tuple[int, str] | None:
    if not _HEADING_INDENT[0] <= line.x0 <= _HEADING_INDENT[1]:
        return None
    match = _ARTICLE.match(text)
    if not match:
        return None
    number = int(match.group(1))
    if number < state.last_article:  # numbers never go back; list items like "1. …" do
        return None
    return number, match.group(2).replace("-", "")


def _end_part(state: _State) -> None:
    """Record a Part that ended without any Article (its heading footnotes explain why)."""
    if state.mode == "parts" and state.part_no and not state.part_has_articles:
        state.close()
        state.empty.append(
            _EmptyDraft(state.part_no, state.part_title, state.part_rows, len(state.done) - 1)
        )
        state.part_has_articles = True  # record once


def _reset(state: _State, mode: _Mode) -> None:
    state.mode = mode
    state.part_no = state.part_title = state.chapter = state.group_heading = None
    state.schedule_no = None
    state.part_rows, state.part_has_articles, state.last_heading = [], False, None


def _appendix_title(lines: Sequence[Line], index: int) -> str:
    """Capitalised rows after `APPENDIX II`, up to the `C.O. 272` / `[28th May, 2015.]` row."""
    title: list[str] = []
    for line in lines[index + 1 : index + 5]:
        text = plain(line)
        if _APPENDIX_TITLE_END.match(text) or text.upper() != text:
            break
        title.append(text)
    return " ".join(" ".join(title).split())


def _is_appendix_title_row(state: _State, text: str) -> bool:
    """The title rows were captured by `_appendix_title`; don't repeat them in the body."""
    draft = state.draft
    if draft is None or draft.rows or not draft.part_title or not text:
        return False
    return " ".join(text.split()) in draft.part_title


def _is_heading_row(state: _State, line: Line, text: str) -> bool:
    """Centered heading rows, plus ALL-CAPS rows right after a PART heading: long Part titles
    start left of centre (Part XI at x0 194)."""
    if not _is_heading_text(text) or _ARTICLE.match(text):
        return False
    return _is_centered(line) or (state.last_heading == "part" and text.upper() == text)


def _is_centered(line: Line) -> bool:
    return line.x0 >= _CENTER_X0


def _is_heading_text(text: str) -> bool:
    """Headings start with a capital. Centered sub-clauses start with "(" and the one centered
    oath row ("solemnly affirm") is lower case."""
    return bool(text) and text[0].isupper() and len(text) <= 90


def _title_case(text: str) -> str:
    words = text.strip(" []").lower().split()
    return " ".join(
        word if i and word in _SMALL_WORDS else word[:1].upper() + word[1:]
        for i, word in enumerate(words)
    )


def _join(existing: str | None, addition: str) -> str:
    return f"{existing} {addition}".strip() if existing else addition


def split_title(rows: Sequence[Line]) -> tuple[str | None, list[Line]]:
    """`21. Protection of life and personal liberty.—No person…` → title + body rows.

    The title ends at the first "—"; it may wrap over up to three rows. Body rows keep their
    footnote tokens; the row holding the dash is replaced by its text after the dash.
    """
    heading = ""
    for k, row in enumerate(rows[:_MAX_TITLE_ROWS]):
        before, dash, after = row.text.partition("—")
        heading = f"{heading} {before}".strip()
        if dash:
            body = [replace(row, text=after.strip())] if after.strip() else []
            return _clean_title(heading), body + list(rows[k + 1 :])
    return (_clean_title(rows[0].text) if rows else None), list(rows[1:])


def _clean_title(heading: str) -> str:
    title = _TITLE_PREFIX.sub("", " ".join(strip_footnote_tokens(heading).split()))
    return title.strip(" .[]")


def _footnotes_for(
    rows: Sequence[Line], index: FootnoteIndex, orphans: list[tuple[int, int]]
) -> tuple[Footnote, ...]:
    """Footnotes referenced by tokens in `rows`, in order, plus editorial notes on their pages."""
    found: dict[tuple[int, int], Footnote] = {}
    for row in rows:
        note = index.get((row.page, EDITORIAL_NOTE))
        if note:
            found.setdefault((row.page, EDITORIAL_NOTE), note)
        for token in FOOTNOTE_TOKEN_RE.finditer(row.text):
            key = (row.page, int(token.group(1)))
            if key in index:
                found.setdefault(key, index[key])
            elif key not in orphans:
                orphans.append(key)
    return tuple(found.values())


def _is_omitted(body: Sequence[Line]) -> bool:
    start = " ".join(strip_footnote_tokens(row.text) for row in body[:2])
    return bool(_OMITTED_BODY.match(start))


def _finish(state: _State, index: FootnoteIndex) -> SegmentResult:
    orphans: list[tuple[int, int]] = []
    segments: list[Segment] = []
    for seq, draft in enumerate(state.done):
        title, body = split_title(draft.rows) if draft.kind == "article" else (None, draft.rows)
        segments.append(
            Segment(
                kind=draft.kind,
                seq=seq,
                lines=tuple(body),
                footnotes=_footnotes_for(draft.rows, index, orphans),
                part_no=draft.part_no,
                part_title=draft.part_title,
                chapter=draft.chapter,
                group_heading=draft.group_heading,
                article_no=draft.article_no,
                article_title=title,
                schedule_no=draft.schedule_no,
                schedule_list=draft.schedule_list,
                appendix_no=draft.appendix_no,
                is_omitted=draft.kind == "article" and _is_omitted(body),
            )
        )
    empty_parts = [
        EmptyPart(e.part_no, e.part_title, _footnotes_for(e.rows, index, orphans), e.after)
        for e in state.empty
    ]
    return SegmentResult(segments, empty_parts, orphans)


def add_omitted_stubs(result: SegmentResult, missing_omitted: Sequence[str]) -> list[Segment]:
    """Segments with a stub for each omitted Article that has no text in the body.

    Part VII was omitted as a whole, so Article 238 appears only in the Contents as
    "[238. Omitted.]". Its stub carries that wording, the Part and the Part heading's footnote,
    and is placed where the empty Part was. Other segments keep their order; seq is renumbered.
    """
    stubs: dict[int, list[Segment]] = {}
    for part, article_no in zip(result.empty_parts, missing_omitted, strict=False):
        page = part.footnotes[0].page if part.footnotes else 0
        stub_line = Line(
            page=page, text="Omitted.", size=0.0, bold_prefix="", y=0.0, x0=0.0, parts=()
        )
        stubs.setdefault(part.after_seq, []).append(
            Segment(
                kind="article",
                seq=0,
                lines=(stub_line,),
                footnotes=part.footnotes,
                part_no=part.part_no,
                part_title=part.part_title,
                article_no=article_no,
                is_omitted=True,
            )
        )
    ordered: list[Segment] = []
    for segment in result.segments:
        ordered.append(segment)
        ordered.extend(stubs.get(segment.seq, []))
    return [replace(segment, seq=seq) for seq, segment in enumerate(ordered)]
