"""Segment step (spec: ingestion §3.4, §7): structure, Article headings, Schedules, Appendices."""

from samvidhan.ingestion.footnotes import EDITORIAL_NOTE, Footnote
from samvidhan.ingestion.segment import (
    Segment,
    SegmentResult,
    add_omitted_stubs,
    segment_lines,
    split_title,
)
from samvidhan.ingestion.types import Line, strip_footnote_tokens
from tests.unit.ingestion_helpers import CENTER, INDENT, row

PREAMBLE = [
    row("PREAMBLE", x0=CENTER, bold="PREAMBLE", page=32),
    row("WE, THE PEOPLE OF INDIA, having solemnly resolved", page=32),
]


def part(no: str, title: str, *, bold: bool = True) -> list[Line]:
    heading = f"PART {no}"
    return [row(heading, x0=CENTER, bold=heading if bold else ""), row(title, x0=CENTER)]


def article(heading: str, body: str = "", *, bold: str | None = None, x0: float = INDENT) -> Line:
    number_and_title = heading.split("—")[0]
    return row(
        heading + (f"—{body}" if body else ""),
        x0=x0,
        bold=number_and_title if bold is None else bold,
    )


def run(*lines: Line, footnotes: dict[tuple[int, int], Footnote] | None = None) -> SegmentResult:
    return segment_lines([*PREAMBLE, *lines], footnotes or {})


def articles(result: SegmentResult) -> list[Segment]:
    return [s for s in result.segments if s.kind == "article"]


def body(segment: Segment) -> list[str]:
    return [strip_footnote_tokens(line.text) for line in segment.lines]


def test_preamble_then_part_and_plain_article() -> None:
    result = run(
        *part("III", "FUNDAMENTAL RIGHTS"),
        row("Right to Freedom", x0=CENTER),
        article("21. Protection of life and personal liberty.", "No person shall be"),
        row("deprived of his life."),
    )
    preamble, art21 = result.segments
    assert preamble.kind == "preamble" and body(preamble) == [
        "WE, THE PEOPLE OF INDIA, having solemnly resolved"
    ]
    assert (art21.article_no, art21.article_title) == (
        "21",
        "Protection of life and personal liberty",
    )
    assert (art21.part_no, art21.part_title, art21.group_heading) == (
        "III",
        "Fundamental Rights",
        "Right to Freedom",
    )
    assert body(art21) == ["No person shall be", "deprived of his life."]
    assert not art21.is_omitted


def test_inserted_lettered_article_after_a_footnote_marker() -> None:
    [art] = articles(
        run(
            *part("III", "FUNDAMENTAL RIGHTS"),
            article(
                "{{fn:2}}[21A. Right to education.",
                "The State shall",
                bold="[21A. Right to education.",
            ),
        )
    )
    assert (art.article_no, art.article_title) == ("21A", "Right to education")


def test_three_letter_and_hyphenated_numbers() -> None:
    result = run(
        *part("IXB", "THE CO-OPERATIVE SOCIETIES"),
        article("243ZH. Definitions.", "In this Part"),
        article("{{fn:1}}[243Z-I. Incorporation of co-operative societies.", "Subject to"),
    )
    assert [a.article_no for a in articles(result)] == ["243ZH", "243ZI"]


def test_omitted_articles_with_bracketed_titles_and_no_bold() -> None:
    result = run(
        *part("I", "THE UNION AND ITS TERRITORY"),
        article(
            "{{fn:3}}[2A. [Sikkim to be associated with the Union.] ", "Omitted by the", bold=""
        ),
        *part("III", "FUNDAMENTAL RIGHTS"),
        article(
            "31. [Compulsory acquisition of property.].", "Omitted by the Constitution", bold="31"
        ),
    )
    omitted = articles(result)
    assert [(a.article_no, a.article_title, a.is_omitted) for a in omitted] == [
        ("2A", "Sikkim to be associated with the Union", True),
        ("31", "Compulsory acquisition of property", True),
    ]


def test_title_wrapping_over_two_rows() -> None:
    [art] = articles(
        run(
            *part("II", "CITIZENSHIP"),
            article(
                "6. Rights of citizenship of certain persons who have migrated to",
                bold="6. Rights of citizenship of certain persons who have migrated to",
            ),
            row(
                "India from Pakistan.—Notwithstanding anything in article 5,",
                bold="India from Pakistan.",
            ),
            row("a person who"),
        )
    )
    assert (
        art.article_title
        == "Rights of citizenship of certain persons who have migrated to India from Pakistan"
    )
    assert body(art) == ["Notwithstanding anything in article 5,", "a person who"]


def test_long_part_title_left_of_centre_and_chapter_and_group() -> None:
    result = run(
        row("PART XI", x0=CENTER, bold="PART XI"),
        row("RELATIONS BETWEEN THE UNION AND THE STATES", x0=194.0),
        row("CHAPTER I.—LEGISLATIVE RELATIONS", x0=237.0, size=7.0),
        row("Distribution of Legislative Powers", x0=CENTER),
        article(
            "245. Extent of laws made by Parliament and by the Legislatures of States.",
            "(1) Subject to",
        ),
    )
    [art] = articles(result)
    assert art.part_title == "Relations Between the Union and the States"
    assert art.chapter == "Chapter I — Legislative Relations"
    assert art.group_heading == "Distribution of Legislative Powers"


def test_non_bold_bracketed_part_headings() -> None:
    result = run(
        *part("IXA", "THE MUNICIPALITIES", bold=False),
        article("243P. Definitions.", "In this Part"),
    )
    assert articles(result)[0].part_no == "IXA"


def test_numbered_list_items_and_running_references_are_not_articles() -> None:
    result = run(
        *part("V", "THE UNION"),
        article("80. Composition of the Council of States.", "(1) The Council"),
        row("1. Nothing in this article", x0=INDENT),  # number goes backwards: list item
        row("article 5 shall apply", x0=MARGIN_X),  # running text
        row("(2) The allocation of seats", x0=INDENT),  # clause, not an Article
    )
    [art] = articles(result)
    assert art.article_no == "80"
    assert len(art.lines) == 4


MARGIN_X = 162.0


def test_centered_subclauses_and_lowercase_rows_stay_in_the_body() -> None:
    result = run(
        *part("VI", "THE STATES"),
        article("159. Oath or affirmation by the Governor.", "Every Governor"),
        row("solemnly affirm", x0=268.0),
        row("(i) the distribution between the State and the Panchayats", x0=222.0),
    )
    [art] = articles(result)
    assert body(art)[-2:] == [
        "solemnly affirm",
        "(i) the distribution between the State and the Panchayats",
    ]
    assert art.group_heading is None


def test_schedules_and_seventh_schedule_lists() -> None:
    result = run(
        *part("XXII", "SHORT TITLE"),
        article("395. Repeals.", "The Indian Independence Act"),
        row("{{fn:1}}[FIRST SCHEDULE", x0=259.0),
        row("[Articles 1 and 4]"),
        row("SEVENTH SCHEDULE", x0=258.0, bold="SEVENTH SCHEDULE"),
        row("(Article 246)", x0=CENTER),
        row("List I—Union List", x0=CENTER, bold="List I—Union List"),
        row("1. Defence of India.", x0=176.0),
        row("List II—State List", x0=CENTER, bold="List II—State List"),
        row("1. Public order.", x0=176.0),
    )
    schedules = [
        (s.schedule_no, s.schedule_list, body(s)) for s in result.segments if s.kind == "schedule"
    ]
    assert schedules == [
        ("1", None, ["[Articles 1 and 4]"]),
        ("7", None, ["(Article 246)"]),
        ("7", "I", ["1. Defence of India."]),
        ("7", "II", ["1. Public order."]),
    ]
    assert [s.article_no for s in articles(result)] == ["395"]  # "1. Defence…" is not an Article


def test_appendix_turns_structure_rules_off() -> None:
    result = run(
        row("TWELFTH SCHEDULE", x0=CENTER, bold="TWELFTH SCHEDULE"),
        row("1. Urban planning.", x0=176.0),
        row("APPENDIX I", x0=CENTER, bold="APPENDIX I"),
        row("THE CONSTITUTION ( ONE HUNDREDTH AMENDMENT)", x0=200.0),
        row("ACT, 2015", x0=CENTER),
        row("[28th May, 2015.]", x0=CENTER),
        row("THE FIRST SCHEDULE", x0=CENTER),
        row("PA R T I", x0=CENTER),
        row("3. Amendment of the First Schedule.", x0=INDENT, bold="3. Amendment"),
        row("APPENDIX  II", x0=CENTER, bold="APPENDIX II"),
        row("{{fn:1}}THE CONSTITUTION (APPLICATION TO JAMMU AND KASHMIR)", x0=175.0),
        row("ORDER, 2019", x0=CENTER),
        row("C.O. 272", x0=CENTER),
    )
    appendices = [s for s in result.segments if s.kind == "appendix"]
    assert [(a.appendix_no, a.part_title) for a in appendices] == [
        ("I", "THE CONSTITUTION ( ONE HUNDREDTH AMENDMENT) ACT, 2015"),
        ("II", "THE CONSTITUTION (APPLICATION TO JAMMU AND KASHMIR) ORDER, 2019"),
    ]
    assert body(appendices[0]) == [
        "[28th May, 2015.]",
        "THE FIRST SCHEDULE",
        "PA R T I",
        "3. Amendment of the First Schedule.",
    ]
    assert body(appendices[1]) == ["C.O. 272"]
    assert not articles(result)


def test_footnotes_attach_to_the_segment_holding_the_marker() -> None:
    notes = {
        (40, 1): Footnote(40, 1, "Subs. by the Constitution (First Amendment) Act, 1951."),
        (40, EDITORIAL_NOTE): Footnote(40, 0, "See also art. 22."),
    }
    result = run(
        *part("III", "FUNDAMENTAL RIGHTS"),
        article("19. Protection of certain rights.", "(1) All citizens {{fn:1}}[shall]"),
        row("have the right {{fn:9}}"),  # no footnote 9 on this page
        footnotes=notes,
    )
    [art] = articles(result)
    assert [n.n for n in art.footnotes] == [0, 1]
    assert result.orphan_markers == [(40, 9)]


def test_empty_part_gets_a_stub_for_its_omitted_article() -> None:
    notes = {
        (142, 1): Footnote(142, 1, "Omitted by the Constitution (Seventh Amendment) Act, 1956.")
    }
    result = run(
        *part("VI", "THE STATES"),
        article("237. Application of provisions.", "The Governor"),
        row("PART VII", x0=CENTER, bold="PART VII", page=142),
        row("{{fn:1}}[The States in Part B of the First Schedule].", x0=228.0, page=142),
        row("PART VIII", x0=CENTER, bold="PART VIII", page=143),
        row("THE UNION TERRITORIES", x0=CENTER, page=143),
        article("239. Administration of Union territories.", "(1) Save as"),
        footnotes=notes,
    )
    assert [(e.part_no, e.part_title) for e in result.empty_parts] == [
        ("VII", "The States in Part B of the First Schedule")
    ]
    segments = add_omitted_stubs(result, ["238"])
    stub = next(s for s in segments if s.article_no == "238")
    assert (stub.part_no, stub.is_omitted, body(stub)) == ("VII", True, ["Omitted."])
    assert [n.text for n in stub.footnotes] == [notes[(142, 1)].text]
    assert [s.article_no for s in segments if s.kind == "article"] == ["237", "238", "239"]
    assert [s.seq for s in segments] == list(range(len(segments)))


def test_split_title_without_a_dash_keeps_the_first_row_as_title() -> None:
    title, rest = split_title([row("99. Oath or affirmation by members"), row("body")])
    assert title == "Oath or affirmation by members"
    assert [r.text for r in rest] == ["body"]
