"""Citation extraction and validation (spec: llm-router-generation §3.8, HLD §8.5).

Citations are bracketed refs: `[Art. 21]`, `[Art. 14, 21(1)]`, `[Arts. 32 and 226]`, `[Sch. 7]`,
`[Preamble]`, `[App. I]`. Any ref not among the retrieved chunks is removed from the text and
logged as `invalid_citation` — that's the hallucinated-citation rate.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from samvidhan.core.logging import get_logger
from samvidhan.retrieval.lookup import normalize_ref
from samvidhan.retrieval.types import ScoredChunk

log = get_logger(__name__)

_BRACKET = re.compile(r"\[([^\[\]\n]{1,160})\]")
_SPLIT = re.compile(r"\s*(?:[,;]|\band\b|&)\s*", re.IGNORECASE)
_PREFIX = re.compile(
    r"^(?P<kind>articles?|arts?\.?|sch(?:edule)?s?\.?|app(?:endix)?\.?|preamble)\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

_ORDINAL_SCHEDULE = re.compile(r"^\w+\s+schedule$", re.IGNORECASE)

TRUNCATION_NOTE = "This answer was shortened — ask me to continue or narrow the question."


@dataclass(frozen=True, slots=True)
class Citation:
    ref: str  # canonical: 21A, SCH-7, PREAMBLE, APP-I
    label: str  # Art. 21A, Sch. 7, Preamble, App. I
    title: str | None = None


@dataclass(frozen=True, slots=True)
class CitationCheck:
    text: str  # answer with invalid citations removed
    citations: list[Citation]  # valid, unique, in order of first mention
    raw_refs: list[str] = field(default_factory=list)  # every ref cited, before validation
    invalid_refs: list[str] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, int]:
        return {"n_raw": len(self.raw_refs), "n_valid": len(self.raw_refs) - len(self.invalid_refs)}


def label(ref: str) -> str:
    if ref == "PREAMBLE":
        return "Preamble"
    if ref.startswith("SCH-"):
        return f"Sch. {ref.removeprefix('SCH-')}"
    if ref.startswith("APP-"):
        return f"App. {ref.removeprefix('APP-')}"
    return f"Art. {ref}"


def _kind(prefix: str) -> str:
    lowered = prefix.lower()
    if lowered.startswith("sch"):
        return "schedule"
    if lowered.startswith("app"):
        return "appendix"
    if lowered.startswith("pre"):
        return "preamble"
    return "article"


def parse_bracket(content: str) -> list[str] | None:
    """Canonical refs in one bracket, or None when it isn't a citation (e.g. `[sic]`)."""
    if not _PREFIX.match(content.strip()):
        if _ORDINAL_SCHEDULE.match(content.strip()):  # [Seventh Schedule]
            ref = normalize_ref(content)
            return [ref] if ref else None
        return None
    refs: list[str] = []
    kind = "article"
    for item in _SPLIT.split(content.strip()):
        if not item:
            continue
        if match := _PREFIX.match(item):
            kind = _kind(match.group("kind"))
            item = match.group("rest").strip(" .")
        raw = {"schedule": f"Schedule {item}", "appendix": f"Appendix {item}"}.get(kind, item)
        ref = "PREAMBLE" if kind == "preamble" else normalize_ref(raw)
        if ref is not None and ref not in refs:
            refs.append(ref)
    return refs


def validate_citations(answer: str, context: Sequence[ScoredChunk]) -> CitationCheck:
    allowed = {c.ref: c for c in context if c.ref is not None}
    raw: list[str] = []
    invalid: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        refs = parse_bracket(match.group(1))
        if refs is None:
            return match.group(0)
        raw.extend(refs)
        bad = [r for r in refs if r not in allowed]
        invalid.extend(bad)
        if not bad:
            return match.group(0)
        kept = [label(r) for r in refs if r in allowed]
        return f"[{'; '.join(kept)}]" if kept else ""

    text = _BRACKET.sub(rewrite, answer)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)  # a removed bracket must not leave "liberty ."
    if invalid:
        log.warning("invalid_citation", cited=_unique(invalid), retrieved_refs=sorted(allowed))
    citations = [
        Citation(ref, label(ref), allowed[ref].article_title)
        for ref in _unique(raw)
        if ref in allowed
    ]
    return CitationCheck(text=text, citations=citations, raw_refs=raw, invalid_refs=invalid)


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def disclaimer(edition: date | None) -> str:
    as_on = f" (as on {edition.day} {edition:%B %Y})" if edition else ""
    return (
        f"_Informational only, based on the text of the Constitution of India{as_on}. "
        "Not legal advice._"
    )
