"""Expected-Article list from the Contents (spec: ingestion §3.11)."""

from pathlib import Path

from samvidhan.ingestion.contents import (
    ExpectedArticle,
    format_fixture,
    load_fixture,
    parse_contents,
)
from tests.unit.ingestion_helpers import row


def test_contents_entries_are_parsed_and_normalised() -> None:
    lines = [
        row("21A. Right to education.", page=5),
        row("[31. Compulsory acquisition of property —Omitted.]", page=6),
        row("243-I. Constitution of Finance Commission to review financial", page=18),
        row("243Z-O. Right of a member to get information.", page=19),
        row("371B . Special provision with respect to the State of Assam.", page=28),
        row("Right to Freedom", page=5),  # group heading: ignored
        row("1. Name and territory of the Union.", page=33),  # body page: ignored
    ]
    assert parse_contents(lines, body_start_page=32) == [
        ExpectedArticle("21A", False),
        ExpectedArticle("31", True),
        ExpectedArticle("243I", False),
        ExpectedArticle("243ZO", False),
        ExpectedArticle("371B", False),
    ]


def test_fixture_round_trip(tmp_path: Path) -> None:
    articles = [ExpectedArticle("21", False), ExpectedArticle("31", True)]
    path = tmp_path / "expected.txt"
    path.write_text(format_fixture(articles) + "# a comment line\n\n")
    assert path.read_text().startswith("21\n31  # omitted\n")
    assert load_fixture(path) == articles


def test_committed_fixture_has_every_article() -> None:
    fixture = Path(__file__).resolve().parents[2] / "eval" / "fixtures" / "expected_articles.txt"
    articles = load_fixture(fixture)
    numbers = [a.article_no for a in articles]
    assert len(numbers) == len(set(numbers)) == 506
    assert {"1", "21A", "243ZH", "371I", "395"} <= set(numbers)
    assert {a.article_no for a in articles if a.is_omitted} >= {"2A", "31", "238", "259"}
