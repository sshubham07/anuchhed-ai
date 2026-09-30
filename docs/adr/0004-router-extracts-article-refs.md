# ADR-0004: Router LLM extracts Article references (no separate regex step)

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
Users write references many ways ("Art. 21", "21-A", "article twenty-one") and use pronouns ("that article").

## Decision
The router returns `article_refs`; code normalizes them to canonical ids and validates against the known Article list. Matching chunks are fetched by metadata and pinned into the context.

## Alternatives considered
- Regex pre-check — fast but misses spelled-out numbers and pronouns.

## Consequences
Exact lookups rely on router quality, so article_refs F1 is gated in eval (≥ 0.95).

## Revisit when
Router refs F1 falls below the gate.
