# ADR-0005: PostgreSQL + pgvector as the single data store

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
We need vector search, full-text search, chat history and telemetry. The corpus is small (~1.5K chunks).

## Decision
Use one PostgreSQL 16 instance with pgvector (HNSW) and built-in FTS for everything.

## Alternatives considered
- Qdrant/Chroma + Postgres — two systems to run and back up.
- MongoDB/DynamoDB for chat — no joins with chunks; fixed message shape doesn't need flexible schema.

## Consequences
One connection pool, one backup, SQL analytics over `llm_calls`. Scale limits are far above our needs.

## Revisit when
Over ~1M vectors or sustained high QPS.
