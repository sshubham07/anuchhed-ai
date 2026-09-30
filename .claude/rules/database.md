---
paths:
  - "src/samvidhan/db/**"
  - "migrations/**"
  - "alembic/**"
---

# Database rules

- Spec: HLD §10. Decision: ADR-0005.
- Schema changes only via Alembic; one migration per PR; `downgrade()` must work (tested in integration).
- Timestamps `TIMESTAMPTZ` in UTC. Session ids UUIDv7; message ids BIGSERIAL.
- Repositories in `db/repositories/`; routers never touch the ORM directly.
- Never store raw IPs, API keys or secrets. IPs only as salted hashes.
