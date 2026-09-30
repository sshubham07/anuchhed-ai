"""Footnotes step (spec: ingestion §3.2, §3.6)."""

from samvidhan.ingestion.footnotes import (
    EDITORIAL_NOTE,
    Footnote,
    join_rows,
    parse_footnote_area,
    split_footnotes,
)
from tests.unit.ingestion_helpers import row

RULE = "______________________________________________"


def texts(rows: list[str]) -> list:  # type: ignore[type-arg]
    return [row(text, size=7.9) for text in rows]


def test_page_is_split_at_the_rule() -> None:
    lines = [
        row("{{fn:1}}[21A. Right to education.—The State shall", page=42),
        row(RULE, page=42, size=12.0),
        row("1. Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002,", page=42, size=7.9),
        row("s. 2 (w.e.f. 1-4-2010).", page=42, size=7.9),
        row("No rule on this page", page=43),
    ]
    body, footnotes = split_footnotes(lines)
    assert [line.text for line in body] == [lines[0].text, "No rule on this page"]
    assert footnotes == {
        (42, 1): Footnote(
            42,
            1,
            "Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002, s. 2 (w.e.f. 1-4-2010).",
        )
    }


def test_numbering_is_by_position_and_skips_year_led_continuations() -> None:
    notes = parse_footnote_area(
        107,
        texts(
            [
                "2. The words ins. by the Tamil Nadu Legislative Council Act, 2010",  # typo: 1
                "(16 of 2010), s. 3.",
                "2. Subs. by the Andhra Pradesh Reorganisation Act,",
                "2014 (6 of 2014), s. 96.",  # starts with a year, not footnote 2014
                "3.Ins. by s. 2, ibid.",  # no space after the dot
            ]
        ),
    )
    assert [(note.n, note.text[:12]) for note in notes] == [
        (1, "The words in"),
        (2, "Subs. by the"),
        (3, "Ins. by s. 2"),
    ]
    assert notes[1].text.endswith("Act, 2014 (6 of 2014), s. 96.")


def test_unnumbered_leading_rows_are_an_editorial_note() -> None:
    notes = parse_footnote_area(
        271, texts(["In P. Sambamurthy v. State of A.P. (1987)", "1 S.C.C. 362."])
    )
    assert [(note.n, note.text) for note in notes] == [
        (EDITORIAL_NOTE, "In P. Sambamurthy v. State of A.P. (1987) 1 S.C.C. 362.")
    ]


def test_as_note_parses_action_act_and_year() -> None:
    note = Footnote(42, 2, "Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002, s. 2.")
    assert note.as_note() == {
        "n": 2,
        "text": note.text,
        "action": "Ins",
        "act": "Constitution (Eighty-sixth Amendment) Act",
        "year": 2002,
    }
    ibid = Footnote(33, 3, "Subs. by s. 2, ibid., for certain words.").as_note()
    assert (ibid["action"], ibid["act"], ibid["year"]) == ("Subs", None, None)


def test_join_rows_glues_hyphenated_line_ends() -> None:
    assert join_rows(["trade or commerce in inter-", "State trade", "and  more"]) == (
        "trade or commerce in inter-State trade and more"
    )
