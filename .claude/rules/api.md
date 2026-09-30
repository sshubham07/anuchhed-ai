---
paths:
  - "src/samvidhan/api/**"
  - "ui/**"
---

# API & UI rules

- Spec: HLD §8.1, §8.4, §11, §12. Standards §3 (API conventions).
- Routes are versioned `/v1/...`; request/response models live in `api/schemas.py`; never return ORM objects.
- Errors use the standard envelope with `code`, `message`, `request_id`.
- SSE event order: `meta` → `token`… → `citations` → `done` (or `error`).
- Contract changes update the OpenAPI snapshot test.
- The UI talks only to the API — no DB or model access from `ui/`.
