"""Chunk step (spec: ingestion §3.5-3.6): paragraphs, splitting, Seventh Schedule groups, texts."""

from samvidhan.ingestion.chunk import ChunkConfig, build_chunks, chunk_header, paragraphs
from samvidhan.ingestion.footnotes import Footnote
from samvidhan.ingestion.segment import Segment
from tests.unit.ingestion_helpers import INDENT, MARGIN, row

CONFIG = ChunkConfig(max_tokens=80, target_min=20, target_max=60, schedule7_entries=2)


def words(text: str) -> int:
    """Stand-in tokenizer: one token per word."""
    return len(text.split())


def art(no: str, *lines, footnotes=(), omitted=False, title="Title") -> Segment:  # type: ignore[no-untyped-def]
    return Segment(
        kind="article",
        seq=0,
        lines=tuple(lines),
        footnotes=tuple(footnotes),
        part_no="III",
        part_title="Fundamental Rights",
        group_heading="Right to Freedom",
        article_no=no,
        article_title=title,
        is_omitted=omitted,
    )


def test_paragraphs_break_on_markers_and_indent_and_join_wrapped_rows() -> None:
    paras = paragraphs(
        [
            row("(1) All citizens shall have the right in inter-", x0=INDENT),
            row("State trade {{fn:1}}[and commerce];"),
            row("(a) to freedom of speech;", x0=198.0),
            row("Provided that nothing", x0=INDENT),
            row("in this clause."),
        ]
    )
    assert [p.text for p in paras] == [
        "(1) All citizens shall have the right in inter-State trade [and commerce];",
        "(a) to freedom of speech;",
        "Provided that nothing in this clause.",
    ]
    assert paras[0].keys == ((40, 1),)


def test_table_rows_join_cells() -> None:
    [first, second] = paragraphs(
        [
            row("8. Nazirganja 41", parts=("8.", "Nazirganja", "41")),
            row("9. Boda 42", parts=("9.", "Boda", "42")),
        ],
        tables=True,
    )
    assert (first.text, second.text) == ("8. | Nazirganja | 41", "9. | Boda | 42")


def test_short_article_is_one_chunk_with_header_and_notes() -> None:
    note = Footnote(42, 2, "Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002.")
    [chunk] = build_chunks(
        [
            art(
                "21A",
                row("The State shall provide free {{fn:2}}education.", page=42),
                footnotes=[note],
                title="Right to education",
            )
        ],
        words,
        CONFIG,
    )
    assert chunk.id == "art-21A#0"
    assert chunk.text == "The State shall provide free education."
    assert chunk.embed_text == (
        "Part III — Fundamental Rights > Right to Freedom > Article 21A: Right to education\n\n"
        "The State shall provide free education.\n\n"
        "Amendment notes:\n- Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002."
    )
    assert chunk.amendment_notes[0]["year"] == 2002
    assert chunk.token_count == words(chunk.embed_text)


def test_long_article_splits_at_clauses_and_repeats_the_header() -> None:
    # Header = 16 words, so pieces get 60 - 16 = 44 tokens; a clause is 16 words.
    clause = " ".join(["word"] * 15)
    lines = [row(f"({n}) {clause}", x0=INDENT) for n in range(1, 6)]
    chunks = build_chunks([art("368", *lines)], words, CONFIG)
    # (1)-(2) | (3)-(4) | (5): the 16-token tail is under target_min and merges into the second.
    assert [c.id for c in chunks] == ["art-368#0", "art-368#1"]
    assert [c.clause_range for c in chunks] == ["(1)-(2)", "(3)-(5)"]
    assert all(c.embed_text.startswith("Part III — Fundamental Rights") for c in chunks)
    assert all(c.token_count <= CONFIG.max_tokens for c in chunks)
    assert [c.seq for c in chunks] == [0, 1]


def test_an_oversized_single_clause_splits_by_sentence() -> None:
    sentence = " ".join(["word"] * 20) + "."
    chunks = build_chunks(
        [art("366", row("(1) " + " ".join([sentence] * 6), x0=INDENT))], words, CONFIG
    )
    assert len(chunks) >= 2
    assert chunks[0].clause_range == "(1)"
    assert all(c.token_count <= CONFIG.max_tokens for c in chunks)


def test_short_tail_is_merged_into_the_previous_piece() -> None:
    lines = [row(f"({n}) " + " ".join(["word"] * 28), x0=INDENT) for n in (1, 2, 3)] + [
        row("(4) short tail", x0=INDENT)
    ]
    chunks = build_chunks([art("19", *lines)], words, CONFIG)
    assert chunks[-1].clause_range.endswith("(4)")  # type: ignore[union-attr]
    assert "(4) short tail" not in chunks[0].text


def test_seventh_schedule_groups_entries() -> None:
    segment = Segment(
        kind="schedule",
        seq=0,
        lines=tuple(row(f"{n}. Entry {n} text.", x0=176.0) for n in (1, 2, 3)),
        schedule_no="7",
        schedule_list="II",
        group_heading="List II—State List",
    )
    chunks = build_chunks([segment], words, CONFIG)
    assert [(c.id, c.clause_range) for c in chunks] == [
        ("sch-7-list2#0", "entries 1-2"),
        ("sch-7-list2#1", "entries 3"),
    ]
    assert chunks[0].embed_text.startswith(
        "Seventh Schedule > List II—State List > Entries 1-2\n\n"
    )


def test_headers_and_ids_for_other_segment_kinds() -> None:
    preamble = Segment(kind="preamble", seq=0, lines=(row("WE, THE PEOPLE"),))
    appendix = Segment(
        kind="appendix",
        seq=1,
        lines=(row("C.O. 272"),),
        appendix_no="II",
        part_title="THE ORDER, 2019",
    )
    schedule = Segment(
        kind="schedule", seq=2, lines=(row("[Articles 1 and 4]", x0=MARGIN),), schedule_no="1"
    )
    assert [chunk_header(s) for s in (preamble, appendix, schedule)] == [
        "Preamble",
        "Appendix II — THE ORDER, 2019",
        "First Schedule",
    ]
    assert [c.id for c in build_chunks([preamble, appendix, schedule], words, CONFIG)] == [
        "preamble#0",
        "app-2#0",
        "sch-1#0",
    ]


def test_omitted_article_keeps_its_flag() -> None:
    [chunk] = build_chunks(
        [
            art(
                "31",
                row("Omitted by the Constitution (Forty-fourth Amendment) Act, 1978."),
                omitted=True,
            )
        ],
        words,
        CONFIG,
    )
    assert chunk.is_omitted and chunk.text.startswith("Omitted by")
