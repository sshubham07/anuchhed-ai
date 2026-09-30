# ADR-0006: Anonymous sessions; authentication later

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
Login adds friction to a public Q&A tool and isn't needed for follow-up questions.

## Decision
Server-issued UUIDv7 session ids, stored client-side. Rate limiting per session and per IP. `user_id` is a nullable column for future auth.

## Alternatives considered
- Mandatory login (OAuth) — friction, more PII to handle.

## Consequences
History is per browser and expires after 30 days of inactivity.

## Revisit when
Cross-device history or per-user quotas are needed.
