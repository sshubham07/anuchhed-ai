"""Pinned lookup: ref normalisation, validation and metadata fetch (spec: retrieval §3.6, ADR-0004).

Canonical ids: Articles `21`, `21A`, `243ZO`; Schedules `SCH-7`; `PREAMBLE`; Appendices `APP-I`.
"""

import re
from collections.abc import Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.retrieval import sql
from samvidhan.retrieval.types import ScoredChunk, chunk_from_row

_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
          "x": 10, "xi": 11, "xii": 12}  # fmt: skip
_ROMAN_OUT = {value: key.upper() for key, value in _ROMAN.items()}

_SCHEDULE_NUM = r"(\d{1,2}|[ivx]+)"
_SCHEDULE_PATTERNS = (
    re.compile(rf"^(?:sch(?:edule)?)[\s.-]*{_SCHEDULE_NUM}$"),
    re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+sch(?:edule)?$"),  # 7th Schedule
    re.compile(r"^([a-z]+)\s+sch(?:edule)?$"),  # Seventh Schedule
)
_APPENDIX = re.compile(r"^app(?:endix)?[\s.-]*(\d|[ivx]+)$")
_ARTICLE_PREFIX = re.compile(r"^(?:article|art)\.?\s*|^a(?=\d)")
_ARTICLE = re.compile(r"^(\d{1,3})([a-z]{0,3})$")


def _number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _ORDINALS.get(token) or _ROMAN.get(token)


def normalize_ref(raw: str) -> str | None:
    """`"Art. 21-A"` → `21A`, `"Seventh Schedule"` → `SCH-7`; unparsable → None (format only)."""
    value = raw.strip().strip(".,;:").lower()
    value = re.sub(r"\s+", " ", value)
    value = value.removeprefix("the ")
    if not value:
        return None
    if value == "preamble":
        return "PREAMBLE"
    if match := _APPENDIX.match(value):
        number = _number(match.group(1))
        return f"APP-{_ROMAN_OUT[number]}" if number in (1, 2, 3) else None
    for pattern in _SCHEDULE_PATTERNS:
        if match := pattern.match(value):
            number = _number(match.group(1))
            return f"SCH-{number}" if number and 1 <= number <= 12 else None
    value = _ARTICLE_PREFIX.sub("", value, count=1)
    value = value.split("(", 1)[0]  # clause suffix: 21(1)(a) → 21
    value = re.sub(r"[\s-]", "", value)
    if match := _ARTICLE.match(value):
        return f"{int(match.group(1))}{match.group(2).upper()}"
    return None


def normalize_refs(raws: Iterable[str]) -> tuple[list[str], list[str]]:
    """Normalised refs in input order without duplicates, plus the raw values that didn't parse."""
    refs: list[str] = []
    rejected: list[str] = []
    for raw in raws:
        ref = normalize_ref(raw)
        if ref is None:
            rejected.append(raw)
        elif ref not in refs:
            refs.append(ref)
    return refs, rejected


async def load_known_refs(session: AsyncSession) -> frozenset[str]:
    """Every canonical ref present in the active document."""
    known: set[str] = set()
    for row in await session.execute(sql.KNOWN_REFS):
        if row.article_no:
            known.add(row.article_no)
        elif row.schedule_no:
            known.add(f"SCH-{row.schedule_no}")
        elif row.appendix_no:
            known.add(f"APP-{row.appendix_no}")
        elif row.chunk_type == "preamble":
            known.add("PREAMBLE")
    return frozenset(known)


async def fetch_pinned(session: AsyncSession, refs: Sequence[str]) -> list[ScoredChunk]:
    """Every chunk of each canonical ref, in reading order."""
    if not refs:
        return []
    params = {
        "articles": [r for r in refs if r[0].isdigit()],
        "schedules": [r.removeprefix("SCH-") for r in refs if r.startswith("SCH-")],
        "appendices": [r.removeprefix("APP-") for r in refs if r.startswith("APP-")],
        "preamble": "PREAMBLE" in refs,
    }
    result = await session.execute(sql.LOOKUP, params)
    return [chunk_from_row(row, pinned=True) for row in result]
