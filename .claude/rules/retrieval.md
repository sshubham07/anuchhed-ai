---
paths:
  - "src/samvidhan/retrieval/**"
  - "src/samvidhan/ingestion/**"
---

# Retrieval & ingestion rules

- Spec: `docs/design/HLD.md` §7 (chunking) and §8.3 (retrieval). Decisions: ADR-0001, ADR-0002, ADR-0004.
- Retrieval is stateless: functions take a standalone query + refs, never chat history.
- Raw SQL for vector/FTS queries lives only in `retrieval/sql.py`, with bound parameters.
- k values, thresholds and model names come from `core/config.py` — no literals.
- Any change here must run `make eval-retrieval` and show the metric diff (no gate below threshold,
  no regression > 2 pts).
- Changing chunking bumps `CHUNKER_VERSION` and requires re-ingestion + ingestion validation.
