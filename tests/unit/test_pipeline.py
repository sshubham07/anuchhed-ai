"""Pipeline helpers (spec: ingestion §3.9)."""

import datetime

from samvidhan.ingestion.pipeline import edition_date
from tests.unit.ingestion_helpers import row


def test_edition_date_from_title_page() -> None:
    assert edition_date([row("THE CONSTITUTION OF INDIA"), row("[As on 1st May, 2024]")]) == (
        datetime.date(2024, 5, 1)
    )


def test_unparseable_or_missing_edition_date_is_none() -> None:
    assert edition_date([row("[As on 1st Sept, 2024]")]) is None
    assert edition_date([row("no date here")]) is None
