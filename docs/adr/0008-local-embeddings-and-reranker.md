# ADR-0008: Local bge-m3 embeddings and bge-reranker-v2-m3

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
We want zero API cost, no data leaving the server for retrieval, and strong multilingual models.

## Decision
Run both models locally (CPU/MPS/CUDA auto), loaded once at startup.

## Alternatives considered
- OpenAI text-embedding-3-small — cheap and good, but a paid dependency.
- Cohere Rerank — strong, but paid and external.

## Consequences
~2–3 GB of weights and ~3.5 GB RAM; rerank latency depends on hardware.

## Revisit when
Rerank p95 exceeds 800 ms on target hardware.
