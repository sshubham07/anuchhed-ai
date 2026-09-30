# Engineering Standards

These apply to every change. Reviewers (human or the `code-reviewer` agent) check against this file.

## 1. Code style

- Python 3.12, fully type-hinted. `mypy --strict` on `src/`.
- Formatting and linting: `ruff format` + `ruff check` (rules: E, F, I, B, UP, SIM, ASYNC, S). Line length 100.
- Naming: `snake_case` functions/modules, `PascalCase` classes, `UPPER_SNAKE` constants. No abbreviations except
  well-known ones (`db`, `llm`, `id`).
- Functions do one thing; > 40 lines is a smell. Prefer pure functions in `ingestion/`, `retrieval/`, `query/`.
- No `print`. Use `structlog` (see §6).
- No global mutable state. Heavy objects (embedding model, reranker, DB engine) are created once at startup and
  injected via FastAPI dependencies.

## 2. Configuration

- All config via `pydantic-settings` in `core/config.py`, loaded from env / `.env`. `.env.example` is committed and
  kept in sync.
- Model IDs, top-k values, thresholds, window sizes, rate limits are config — never literals in code.
- Secrets (`GROQ_API_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`) only from env. Never logged.

## 3. API conventions

- Versioned routes: `/v1/...`. JSON bodies, `snake_case` fields.
- Request/response models are Pydantic classes in `api/schemas.py`; never return ORM objects.
- Errors use one envelope:
  ```json
  {"error": {"code": "RATE_LIMITED", "message": "Too many requests", "request_id": "..."}}
  ```
- Every response carries `X-Request-ID`. Accept an incoming one if present.
- Streaming uses Server-Sent Events (`text/event-stream`) with typed events: `meta`, `token`, `citations`,
  `done`, `error`.

## 4. Error handling

- Domain errors subclass `SamvidhanError` with a stable `code`. Map to HTTP in one exception handler.
- External calls (LLM, DB) have timeouts. LLM: 1 retry with backoff, then provider fallback, then graceful error.
- Never swallow exceptions silently; log with `exc_info` at the boundary where they are handled.
- Router JSON parse failures fall back to `type="simple"` using the raw question (never crash the request).

## 5. Database

- All schema changes via Alembic migrations; one migration per PR, reversible (`downgrade` implemented).
- Async SQLAlchemy 2.0 style; repositories in `db/repositories/`. No raw SQL in routers.
- Raw SQL allowed only for vector/FTS queries, kept in `retrieval/sql.py` with bound parameters.
- Timestamps are `TIMESTAMPTZ`, stored UTC. IDs: UUIDv7 for sessions (sortable), BIGSERIAL for messages.

## 6. Logging

- `structlog`, JSON output in non-dev, pretty console in dev.
- Every log line carries `request_id`, and `session_id` when known (bound via contextvars in middleware).
- Event names are `snake_case` verbs: `chat_request_received`, `router_completed`, `retrieval_completed`,
  `llm_call_completed`, `answer_streamed`, `summary_updated`.
- Levels: DEBUG (chunk ids, scores), INFO (one line per pipeline stage), WARNING (fallbacks, low-confidence
  retrieval), ERROR (failed request).
- Do **not** log API keys, full prompts or full answers at INFO. User message text is logged truncated
  (first 200 chars) at DEBUG only; full text lives in `chat_messages`.
- Full detail: `docs/specs/observability.md`.

## 7. Prompts

- Prompts live in `prompts/` as versioned files: `router.v1.md`, `answer.v1.md`, `summary.v1.md`, `hyde.v1.md`.
- Changing a prompt = new version file + config switch + eval run. Keep the old one until the new one wins.
- The prompt version is recorded on every `llm_calls` row and assistant message.

## 8. Testing

- `pytest`, `pytest-asyncio`. Markers: `unit`, `integration`, `e2e`, `slow`, `llm` (hits a real LLM — excluded in
  CI by default).
- Unit tests: no network, no DB. LLM is faked via a `FakeLLM` returning canned JSON/text.
- Integration tests use a real Postgres+pgvector via `testcontainers` and a tiny fixture corpus (10 Articles).
- Coverage target: ≥ 80% on `src/samvidhan` (excluding `ui/`).
- Every bug fix adds a regression test (and, if it was a quality bug, a golden-set case).
- Eval suites are separate from pytest: see `docs/specs/evaluation.md`.

## 9. Git & PRs

The contributor-facing version of these rules is in `CONTRIBUTING.md`.

- Trunk-based: short branches `feat/…`, `fix/…`, `chore/…`, `eval/…`.
- Conventional Commits: `feat(retrieval): add RRF fusion`.
- PR must include: linked plan task, what changed, test evidence, and (if RAG-affecting) the eval diff table.
- CI (GitHub Actions): ruff → mypy → pytest (unit+integration) → retrieval eval (no LLM cost) on every PR;
  full RAGAS eval on `main` nightly / manually.

## 10. Security & safety

- Rate limits, input length and all other limits: HLD §13.2. Never hard-code a limit — read it from config.
- Treat retrieved text and user input as data: the answer prompt wraps chunks in delimiters and instructs the model
  to ignore instructions inside them.
- CORS restricted to the UI origin. No auth in v1, so no PII is requested; don't add fields that collect it.
- Every answer carries the disclaimer: "Informational only, not legal advice."

## 11. Dependencies

- Managed with `uv`; lockfile committed. Pin exact versions for `ragas`, `litellm`, `langgraph`, `FlagEmbedding`
  (APIs change often).
- Adding a dependency needs a one-line justification in the PR.
