"""Lexical query preprocessing (spec: retrieval §3.4)."""

import pytest

from samvidhan.retrieval.lexical import strip_negations


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("police -army", "police"),
        ("-army police", "police"),
        ("right to life", "right to life"),
        ("Article 21-A education", "Article 21-A education"),  # in-word hyphen kept
        ('"personal liberty" -detention', '"personal liberty"'),
    ],
)
def test_strip_negations(query: str, expected: str) -> None:
    assert strip_negations(query) == expected
