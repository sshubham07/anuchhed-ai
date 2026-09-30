# ADR-0002: Hybrid retrieval (dense + Postgres FTS, RRF) with a cross-encoder reranker

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
Dense embeddings handle plain-language questions but are weak at exact tokens such as "21A" or "Tenth Schedule". Keyword search is the opposite.

## Decision
Run dense (bge-m3, pgvector HNSW) and lexical (Postgres FTS) search in parallel, merge with Reciprocal Rank Fusion, then rerank the top 15 with bge-reranker-v2-m3 and keep the top 5.

## Alternatives considered
- Dense only — misses exact references.
- BM25 via a separate engine (Elasticsearch/OpenSearch) — extra infra for ~1.5K chunks.
- bge-m3 sparse vectors — promising; revisit in v2 if eval shows a gain.

## Consequences
Higher recall and precision at the cost of ~300–800 ms of reranking on CPU.

## Revisit when
Ablations show one leg adds nothing, or rerank latency misses the SLO.
