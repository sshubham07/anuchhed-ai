"""Ref normaliser (spec: retrieval §3.6)."""

import pytest

from samvidhan.retrieval.lookup import normalize_ref, normalize_refs


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("21", "21"),
        ("Art. 21", "21"),
        ("Article 21", "21"),
        ("article21", "21"),
        ("A21", "21"),
        ("Art 21.", "21"),
        ("21-A", "21A"),
        ("21 A", "21A"),
        ("21a", "21A"),
        ("Art. 21-A", "21A"),
        ("243-I", "243I"),
        ("243Z-O", "243ZO"),
        ("21(1)(a)", "21"),
        ("Article 15(4)", "15"),
        ("SCH-7", "SCH-7"),
        ("Schedule 7", "SCH-7"),
        ("Schedule VII", "SCH-7"),
        ("Seventh Schedule", "SCH-7"),
        ("7th Schedule", "SCH-7"),
        ("tenth schedule", "SCH-10"),
        ("Preamble", "PREAMBLE"),
        ("Appendix II", "APP-II"),
        ("APP-2", "APP-II"),
        ("  article   370 ", "370"),
    ],
)
def test_normalize_ref(raw: str, expected: str) -> None:
    assert normalize_ref(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "foo", "Section 302", "Thirteenth Schedule", "Schedule 13", "Appendix IV", "21AAAA"]
)
def test_unparsable_refs_are_none(raw: str) -> None:
    assert normalize_ref(raw) is None


def test_normalize_refs_dedupes_and_reports_rejects() -> None:
    refs, rejected = normalize_refs(["Art. 21-A", "21A", "Seventh Schedule", "foo", "14"])
    assert refs == ["21A", "SCH-7", "14"]
    assert rejected == ["foo"]


def test_leading_article_is_ignored() -> None:
    assert normalize_ref("the Seventh Schedule") == "SCH-7"
    assert normalize_ref("The Preamble") == "PREAMBLE"
