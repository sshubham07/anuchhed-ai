"""Citation extraction, validation and disclaimer (spec §3.8)."""

from datetime import date

import pytest
import structlog.testing

from samvidhan.generation.citations import (
    disclaimer,
    label,
    parse_bracket,
    validate_citations,
)
from tests.unit.retrieval_helpers import chunk

CONTEXT = [
    chunk("art-21#0", article_no="21"),
    chunk("art-21A#0", article_no="21A"),
    chunk("art-22#0", article_no="22"),
    chunk("sch-7-list2#0", schedule_no="7"),
]


@pytest.mark.parametrize(
    ("content", "refs"),
    [
        ("Art. 21", ["21"]),
        ("Article 21-A", ["21A"]),
        ("Art. 14, 21(1)(a)", ["14", "21"]),
        ("Arts. 32 and 226", ["32", "226"]),
        ("Art. 14; Art. 21", ["14", "21"]),
        ("Sch. 7", ["SCH-7"]),
        ("Seventh Schedule", ["SCH-7"]),
        ("10th Schedule", ["SCH-10"]),
        ("the schedule", None),
        ("Schedule VII", ["SCH-7"]),
        ("Preamble", ["PREAMBLE"]),
        ("App. I", ["APP-I"]),
        ("Art. foo", []),  # citation-shaped but nothing parseable: no refs, text untouched
        ("sic", None),
        ("1", None),
    ],
)
def test_parse_bracket(content: str, refs: list[str] | None) -> None:
    assert parse_bracket(content) == refs


@pytest.mark.parametrize(
    ("ref", "text"),
    [("21A", "Art. 21A"), ("SCH-7", "Sch. 7"), ("PREAMBLE", "Preamble"), ("APP-II", "App. II")],
)
def test_label(ref: str, text: str) -> None:
    assert label(ref) == text


def test_valid_citations_are_kept_in_first_mention_order() -> None:
    check = validate_citations(
        "Arrest rights [Art. 22]. Life [Art. 21]. Again [Art. 22]. Police [Sch. 7].", CONTEXT
    )
    assert [c.ref for c in check.citations] == ["22", "21", "SCH-7"]
    assert check.invalid_refs == [] and check.stats == {"n_raw": 4, "n_valid": 4}
    assert check.text.startswith("Arrest rights [Art. 22].")


def test_invalid_citations_are_removed_and_logged() -> None:
    answer = "Free Wi-Fi is not a right [Art. 99]. Education [Art. 21A, 45]. Life [Art. 21]."
    with structlog.testing.capture_logs() as logs:
        check = validate_citations(answer, CONTEXT)
    assert check.text == "Free Wi-Fi is not a right. Education [Art. 21A]. Life [Art. 21]."
    assert check.invalid_refs == ["99", "45"]
    assert check.stats == {"n_raw": 4, "n_valid": 2}
    event = next(e for e in logs if e["event"] == "invalid_citation")
    assert event["cited"] == ["99", "45"] and "21A" in event["retrieved_refs"]


def test_non_citation_brackets_untouched() -> None:
    check = validate_citations("He said [sic] that [1] applies.", CONTEXT)
    assert check.text == "He said [sic] that [1] applies." and check.citations == []


def test_disclaimer_names_the_edition() -> None:
    assert "(as on 1 May 2024)" in disclaimer(date(2024, 5, 1))
    assert "Not legal advice" in disclaimer(None) and "as on" not in disclaimer(None)
