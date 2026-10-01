"""The committed golden set is well-formed and its refs exist (spec: evaluation.md §2)."""

from pathlib import Path

from eval.golden import load_cases

ROOT = Path(__file__).resolve().parents[2]


def _known_refs() -> set[str]:
    lines = (ROOT / "eval/fixtures/expected_articles.txt").read_text().splitlines()
    articles = {line.split("#")[0].strip() for line in lines if line.split("#")[0].strip()}
    return (
        articles | {f"SCH-{n}" for n in range(1, 13)} | {"PREAMBLE", "APP-I", "APP-II", "APP-III"}
    )


def test_golden_cases_load_and_refs_exist() -> None:
    cases = load_cases()
    known = _known_refs()
    assert len(cases) >= 60
    for case in cases:
        refs = {*case.expected_refs, *case.acceptable_refs, *case.question_refs}
        assert refs <= known, f"{case.id}: unknown refs {refs - known}"
        assert not (case.must_refuse and case.expected_refs), case.id


def test_both_splits_cover_the_retrievable_categories() -> None:
    cases = load_cases()
    for category in ("article_lookup", "simple", "multi_part", "conceptual", "schedule"):
        splits = {c.split for c in cases if c.category == category}
        assert splits == {"dev", "test"}, category
