# ADR-0007: Structured memory in v1; LLM rolling summary in v1.1 behind a flag

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
Follow-ups mostly refer to the last one or two turns and to Articles already discussed. LLM summaries cost calls and can drift.

## Decision
v1 tracks `last_articles`, `articles_discussed`, `parts_discussed` and `recent_topics` in code. v1.1 adds an async rolling summary of messages older than the last 12, enabled only if multi-turn eval is equal or better.

## Alternatives considered
- LLM summary from day one — extra cost and a hallucination source before we know it helps.

## Consequences
Zero-cost memory that can't hallucinate; long chats lose nuance until v1.1.

## Revisit when
Long sessions become common or multi-turn eval fails on early-turn references.
