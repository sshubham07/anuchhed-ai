# ADR-0001: Structure-aware chunking (one Article per chunk)

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
The Constitution is hierarchical (Part → Chapter → Article → Clause) and users ask about Articles by number or topic. Fixed-size chunking cuts Articles in half and mixes unrelated ones, which breaks citations.

## Decision
Chunk one Article per chunk (Preamble as one chunk). Split Articles over 800 tokens on clause boundaries, keeping the Article header on every piece. Keep omitted Articles as chunks flagged `is_omitted`. Schedules are split by list/paragraph.

## Alternatives considered
- Fixed-size 512-token chunks with overlap — simple, but splits Articles and blurs citations.
- Semantic chunking — extra cost and non-deterministic boundaries for a corpus that already has clear structure.

## Consequences
Exact Article-level citations and metadata filters. Parsing must be reliable, so ingestion validates against an expected Article list.

## Revisit when
Recall misses trace back to chunk size or split points.
