"""Chunk step: segments → chunk records with `text`, `embed_text` and metadata (spec §3.5-3.6).

Rules (chunker v1): one chunk per Article; an Article over `max_tokens` is split at top-level
clauses `(1)`, `(2)`… into pieces of `target_min`-`target_max` tokens, each repeating the
header; Seventh Schedule entries are grouped `schedule7_entries` per chunk; other Schedules and
Appendices are packed by paragraph. Table rows (cells in `Line.parts`) are joined with " | ".
Token counts use the embedding model's tokenizer, injected as `count_tokens`.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from samvidhan.ingestion.footnotes import Footnote, join_rows
from samvidhan.ingestion.segment import Segment
from samvidhan.ingestion.types import FOOTNOTE_TOKEN_RE, Line, strip_footnote_tokens

TokenCounter = Callable[[str], int]
FootnoteKey = tuple[int, int]

_PARAGRAPH_START = re.compile(
    r"^[\s\[*]*(\(\w{1,5}\)|Provided\b|Explanation\b|\d{1,3}[A-Z]{0,2}\.\s)"
)
_CLAUSE = re.compile(r"^[\s\[*]*(\(\d{1,2}[A-Z]{0,2}\))")
_ENTRY = re.compile(r"^[\s\[*]*(\d{1,3}[A-Z]{0,2})\.")
_SENTENCE_END = re.compile(r"(?<=[.;:])\s+")
_INDENT_STEP = 4.0  # a row starting this much further right than the previous one opens a paragraph
_ORDINAL = "First Second Third Fourth Fifth Sixth Seventh Eighth Ninth Tenth Eleventh Twelfth"
_LIST_NO = {"I": "1", "II": "2", "III": "3"}


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    max_tokens: int
    target_min: int
    target_max: int
    schedule7_entries: int


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """One row of the `chunks` table, minus document_id / embedding / tsv (HLD §10)."""

    id: str
    chunk_type: str
    seq: int
    part_no: str | None
    part_title: str | None
    chapter: str | None
    group_heading: str | None
    article_no: str | None
    article_title: str | None
    schedule_no: str | None
    appendix_no: str | None
    clause_range: str | None
    is_omitted: bool
    amendment_notes: tuple[dict[str, Any], ...]
    text: str
    embed_text: str
    token_count: int

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["amendment_notes"] = list(self.amendment_notes)
        return row


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str  # footnote tokens removed, whitespace normalised
    keys: tuple[FootnoteKey, ...]  # footnotes referenced inside it


@dataclass(frozen=True, slots=True)
class _Unit:
    """A packable piece: a clause block, a paragraph, an entry group or a sentence run."""

    paragraphs: tuple[Paragraph, ...]
    label: str | None  # "(1)" for a clause block, "12" for a Seventh Schedule entry


def paragraphs(lines: Sequence[Line], *, tables: bool = False) -> list[Paragraph]:
    """Group rows into paragraphs: a new one starts on a clause/proviso/entry marker, on a
    deeper indent, or (for Schedules/Appendices) on every table row."""
    groups: list[list[Line]] = []
    previous: Line | None = None
    for line in lines:
        text = strip_footnote_tokens(line.text)
        if not text:
            continue
        is_table_row = tables and len(line.parts) > 1
        starts = (
            previous is None
            or is_table_row
            or bool(_PARAGRAPH_START.match(text))
            or line.x0 > previous.x0 + _INDENT_STEP
        )
        if starts or not groups:
            groups.append([line])
        else:
            groups[-1].append(line)
        previous = line
    return [_paragraph(group, tables=tables) for group in groups]


def _paragraph(rows: Sequence[Line], *, tables: bool) -> Paragraph:
    keys = tuple(
        (row.page, int(token.group(1)))
        for row in rows
        for token in FOOTNOTE_TOKEN_RE.finditer(row.text)
    )
    texts = [
        " | ".join(strip_footnote_tokens(p) for p in row.parts if strip_footnote_tokens(p))
        if tables and len(row.parts) > 1
        else strip_footnote_tokens(row.text)
        for row in rows
    ]
    return Paragraph(join_rows(texts), keys)


def build_chunks(
    segments: Sequence[Segment], count_tokens: TokenCounter, config: ChunkConfig
) -> list[ChunkRecord]:
    """All chunks in reading order, `seq` numbered across the document."""
    chunks: list[ChunkRecord] = []
    for segment in segments:
        for piece in _segment_chunks(segment, count_tokens, config):
            chunks.append(_with_seq(piece, len(chunks)))
    return chunks


def _segment_chunks(
    segment: Segment, count_tokens: TokenCounter, config: ChunkConfig
) -> list[ChunkRecord]:
    tables = segment.kind in ("schedule", "appendix")
    paras = paragraphs(segment.lines, tables=tables)
    notes = {(note.page, note.n): note for note in segment.footnotes}
    body_keys = {key for para in paras for key in para.keys}
    # Notes on the heading row or editorial notes: referenced by no paragraph; go on piece 0.
    extra = [note for key, note in notes.items() if key not in body_keys]
    header = chunk_header(segment)
    fixed = count_tokens(header) + (count_tokens(_note_text(extra)) if extra else 0)
    if segment.schedule_list:
        groups = _entry_groups(paras, config.schedule7_entries)
    else:
        units = _clause_blocks(paras) if segment.kind == "article" else _single(paras)
        whole = _Unit(tuple(paras), None)
        if _cost(whole, notes, count_tokens) + fixed <= config.max_tokens:
            groups = [[whole]]
        else:
            # A huge header/notes block must not push the budget to zero (one sentence per piece).
            target = max(config.target_max - fixed, config.target_min)
            budget = _Budget(target, config.target_min, max(config.max_tokens - fixed, target))
            groups = _pack(units, notes, count_tokens, budget)
    pieces = [group for group in groups if any(u.paragraphs for u in group)] or [[]]
    return [
        _record(segment, header, piece, notes, index, count_tokens, extra if index == 0 else [])
        for index, piece in enumerate(pieces)
    ]


def _clause_blocks(paras: Sequence[Paragraph]) -> list[_Unit]:
    """Top-level clauses with their sub-clauses, provisos and Explanations."""
    blocks: list[list[Paragraph]] = []
    labels: list[str | None] = []
    for para in paras:
        clause = _CLAUSE.match(para.text)
        if clause or not blocks:
            blocks.append([para])
            labels.append(clause.group(1) if clause else None)
        else:
            blocks[-1].append(para)
    return [_Unit(tuple(block), label) for block, label in zip(blocks, labels, strict=True)]


def _single(paras: Sequence[Paragraph]) -> list[_Unit]:
    return [_Unit((para,), None) for para in paras]


def _entry_groups(paras: Sequence[Paragraph], per_chunk: int) -> list[list[_Unit]]:
    """Seventh Schedule: entries (`12.`, `12A.`) with their continuation paragraphs, N per chunk."""
    entries: list[_Unit] = []
    for para in paras:
        entry = _ENTRY.match(para.text)
        if entry or not entries:
            entries.append(_Unit((para,), entry.group(1) if entry else None))
        else:
            last = entries[-1]
            entries[-1] = _Unit((*last.paragraphs, para), last.label)
    return [entries[i : i + per_chunk] for i in range(0, len(entries), per_chunk)]


@dataclass(frozen=True, slots=True)
class _Budget:
    """Token limits for split pieces, after the header and piece-0 notes are taken off."""

    target_max: int  # pack up to this
    target_min: int  # a last piece below this is merged into the one before…
    merge_max: int  # …if the merged piece stays within the split threshold


def _pack(
    units: Sequence[_Unit],
    notes: dict[FootnoteKey, Footnote],
    count_tokens: TokenCounter,
    budget: _Budget,
) -> list[list[_Unit]]:
    """Greedy packing under the target; oversized units are exploded; a short tail is merged."""
    groups: list[list[_Unit]] = [[]]
    for unit in _explode_all(units, notes, count_tokens, budget.target_max):
        current = groups[-1]
        if current and _group_cost(current + [unit], notes, count_tokens) > budget.target_max:
            groups.append([unit])
        else:
            current.append(unit)
    if len(groups) > 1 and _group_cost(groups[-1], notes, count_tokens) < budget.target_min:
        merged = groups[-2] + groups[-1]
        if _group_cost(merged, notes, count_tokens) <= budget.merge_max:
            groups[-2:] = [merged]
    return groups


def _explode_all(
    units: Sequence[_Unit],
    notes: dict[FootnoteKey, Footnote],
    count_tokens: TokenCounter,
    budget: int,
) -> list[_Unit]:
    """Units over budget become paragraphs, then sentence runs (labels stay on the first)."""
    out: list[_Unit] = []
    for unit in units:
        if _cost(unit, notes, count_tokens) <= budget:
            out.append(unit)
        elif len(unit.paragraphs) > 1:
            parts = [
                _Unit((p,), unit.label if i == 0 else None) for i, p in enumerate(unit.paragraphs)
            ]
            out.extend(_explode_all(parts, notes, count_tokens, budget))
        else:
            out.extend(_sentence_units(unit, notes, count_tokens, budget))
    return out


def _sentence_units(
    unit: _Unit, notes: dict[FootnoteKey, Footnote], count_tokens: TokenCounter, budget: int
) -> list[_Unit]:
    para = unit.paragraphs[0]
    runs: list[str] = [""]
    for sentence in _SENTENCE_END.split(para.text):
        candidate = f"{runs[-1]} {sentence}".strip()
        if runs[-1] and count_tokens(candidate) > budget:
            runs.append(sentence)
        else:
            runs[-1] = candidate
    return [
        _Unit((Paragraph(run, para.keys if i == 0 else ()),), unit.label if i == 0 else None)
        for i, run in enumerate(runs)
    ]


def _cost(unit: _Unit, notes: dict[FootnoteKey, Footnote], count_tokens: TokenCounter) -> int:
    return _group_cost([unit], notes, count_tokens)


def _group_cost(
    group: Sequence[_Unit], notes: dict[FootnoteKey, Footnote], count_tokens: TokenCounter
) -> int:
    body = "\n".join(p.text for unit in group for p in unit.paragraphs)
    referenced = _notes_for(group, notes, [])
    return count_tokens(body) + (count_tokens(_note_text(referenced)) if referenced else 0)


def _notes_for(
    group: Sequence[_Unit], notes: dict[FootnoteKey, Footnote], extra: Sequence[Footnote]
) -> list[Footnote]:
    """Footnotes referenced in the group's paragraphs, plus `extra` (piece 0 only)."""
    keys = [key for unit in group for p in unit.paragraphs for key in p.keys]
    found = {key: notes[key] for key in keys if key in notes}
    for note in extra:
        found.setdefault((note.page, note.n), note)
    return sorted(found.values(), key=lambda note: (note.page, note.n))


def _note_text(notes: Sequence[Footnote]) -> str:
    return "\n".join(f"- {note.text}" for note in notes)


def chunk_header(segment: Segment) -> str:
    """The context path that starts `embed_text` (spec §3.6)."""
    if segment.kind == "preamble":
        return "Preamble"
    if segment.kind == "appendix":
        return f"Appendix {segment.appendix_no} — {_title(segment.part_title)}".rstrip(" —")
    if segment.kind == "schedule":
        name = f"{_ORDINAL.split()[int(segment.schedule_no or 1) - 1]} Schedule"
        return " > ".join(filter(None, [name, segment.group_heading]))
    part = f"Part {segment.part_no} — {segment.part_title}" if segment.part_no else None
    article = f"Article {segment.article_no}"
    if segment.article_title:
        article += f": {segment.article_title}"
    return " > ".join(filter(None, [part, segment.chapter, segment.group_heading, article]))


def _record(
    segment: Segment,
    header: str,
    group: Sequence[_Unit],
    notes: dict[FootnoteKey, Footnote],
    index: int,
    count_tokens: TokenCounter,
    extra: Sequence[Footnote],
) -> ChunkRecord:
    text = "\n".join(p.text for unit in group for p in unit.paragraphs)
    piece_notes = _notes_for(group, notes, extra)
    clause_range = _range(group)
    piece_header = f"{header} > Entries {clause_range}" if segment.schedule_list else header
    embed_text = f"{piece_header}\n\n{text}".strip()
    if piece_notes:
        embed_text += "\n\nAmendment notes:\n" + _note_text(piece_notes)
    return ChunkRecord(
        id=f"{_id_prefix(segment)}#{index}",
        chunk_type=segment.kind,
        seq=0,
        part_no=segment.part_no if segment.kind == "article" else None,
        part_title=segment.part_title,
        chapter=segment.chapter,
        group_heading=segment.group_heading,
        article_no=segment.article_no,
        article_title=segment.article_title,
        schedule_no=segment.schedule_no,
        appendix_no=segment.appendix_no,
        clause_range=f"entries {clause_range}" if segment.schedule_list else clause_range,
        is_omitted=segment.is_omitted,
        amendment_notes=tuple(note.as_note() for note in piece_notes),
        text=text,
        embed_text=embed_text,
        token_count=count_tokens(embed_text),
    )


def _range(group: Sequence[_Unit]) -> str | None:
    """ "(1)-(3)" for clause blocks, "1-10" for Seventh Schedule entries."""
    labels = [unit.label for unit in group if unit.label]
    if not labels:
        return None
    return labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"


def _id_prefix(segment: Segment) -> str:
    if segment.kind == "preamble":
        return "preamble"
    if segment.kind == "appendix":
        return f"app-{_LIST_NO.get(segment.appendix_no or '', segment.appendix_no)}"
    if segment.kind == "schedule":
        list_no = _LIST_NO.get(segment.schedule_list or "", segment.schedule_list)
        suffix = f"-list{list_no}" if segment.schedule_list else ""
        return f"sch-{segment.schedule_no}{suffix}"
    return f"art-{segment.article_no}"


def _title(text: str | None) -> str:
    return " ".join(text.split()) if text else ""


def _with_seq(chunk: ChunkRecord, seq: int) -> ChunkRecord:
    return replace(chunk, seq=seq)
