# Spec: Project foundation (config, logging, database, API skeleton, Makefile)

- **Status:** Implemented on `chore/p0-foundation` — awaiting owner approval
- **Owner:** Shubham
- **Related:** HLD §5, §10, §11, §16, §18 · observability.md §1 · standards §1–6, §8 · ADR-0005 · plan Phase 0
  (P0.1–P0.7, P0.9, P0.11)
- **Last updated:** 2026-09-30

## 1. Problem / goal

Before any RAG code exists we need a production-shaped skeleton: typed config, structured logging with a
`request_id` on every line, a local Postgres + pgvector that doesn't clash with other projects on the same machine,
reversible migrations for the corpus tables, a FastAPI app with health checks and one error envelope, and a
`Makefile` so every workflow is one command.

## 2. Scope

- **In scope:** `pyproject.toml` + tooling config; package skeleton; `core/config.py`, `core/logging.py`,
  `core/errors.py`, `core/ids.py`; `docker-compose.yml` (`db`, `api`); `Dockerfile` for the API; Alembic +
  migration 001 (`documents`, `chunks`); `db/engine.py`; FastAPI app factory, request-id middleware, error handlers,
  `/healthz`, `/readyz`; `Makefile`; `.gitignore`; unit + integration tests for the above.
- **Out of scope:** CI workflow (P0.8), pre-commit config (P0.10), SECURITY/CHANGELOG (P0.12), branch protection
  (P0.13), the `ui` compose service (P6.6), model loading at startup (Phase 1/3), `/v1/*` routes (Phase 5),
  tables other than `documents`/`chunks` (later migrations).

## 3. Design

### 3.1 Local database (Docker)

- Compose project name is pinned (`name: samvidhan`), so the container (`samvidhan-db-1`), network and volume
  (`samvidhan_pgdata`) never collide with another project's compose stack.
- Image `pgvector/pgvector:pg16`. Host port is `${DB_HOST_PORT:-5433}` → container `5432`. **5433 by default**
  because 5432 is commonly taken by another local Postgres; override via `DB_HOST_PORT` in `.env`.
- Healthcheck: `pg_isready`. `make up` waits for `healthy`.
- `DATABASE_URL` in `.env.example` points at `localhost:5433`. Inside compose the `api` service overrides it to
  `db:5432`.
- `CREATE EXTENSION vector` runs in migration 001, not in an init script, so a fresh DB and a test container
  behave the same.

### 3.2 Database access

- `db/engine.py`: `create_engine(settings) -> AsyncEngine` (asyncpg, `pool_pre_ping=True`, pool size from config,
  `connect_args={"timeout": DB_CONNECT_TIMEOUT_S}`) and `create_session_factory(engine)`.
- The engine is created once in the FastAPI lifespan, stored on `app.state`, disposed on shutdown, and injected via
  `api/deps.py` (standards §1: no global mutable state).
- `db/models.py`: SQLAlchemy 2 declarative `Base` with a naming convention for constraints; ORM classes for
  `Document` and `Chunk` matching HLD §10 exactly.
- The `api` compose service sits behind the `app` profile (`docker compose --profile app up`), so `make up`
  starts only the DB. The image runs `alembic upgrade head` then uvicorn (HLD §18).

### 3.3 Migrations

- Alembic in `migrations/`, async env, URL taken from `Settings.database_url` (never from `alembic.ini`).
- `001_corpus`: `CREATE EXTENSION IF NOT EXISTS vector`; `documents`; `chunks` with generated `tsv` column,
  `chunks_embedding_hnsw` (HNSW, `vector_cosine_ops`), `chunks_tsv_gin`, `chunks_article`. `downgrade()` drops the
  tables (the extension is left in place — other objects may depend on it).

### 3.4 Configuration

`core/config.py`: one `Settings(BaseSettings)` class reading env / `.env`, every key in `.env.example`, grouped as
in that file. `get_settings()` is `lru_cache`d and used only at composition roots (app factory, CLI, Alembic);
everything else receives settings or values by injection. Secrets are `SecretStr`. Placeholder model IDs are
accepted as strings (validated when the LLM layer lands).

### 3.5 Logging

Implements observability.md §1.1–1.2 and §1.4.

- `configure_logging(settings)` called once, first thing in the app factory / CLI entry point.
- structlog processor chain: `merge_contextvars` → add static fields (`service`, `env`, `version`) → `add_log_level`
  → ISO-8601 UTC `timestamp` → stack/exception rendering → `redact_secrets` → renderer
  (`JSONRenderer` when `LOG_FORMAT=json`, `ConsoleRenderer` when `console`).
- stdlib logging (uvicorn, sqlalchemy, asyncpg, httpx, urllib3, litellm, alembic) goes through a
  `structlog.stdlib.ProcessorFormatter` on the root handler, so every line — ours or a library's — has the same
  shape and carries the bound `request_id`. uvicorn's own access log is disabled; the middleware emits
  `http_request_completed` instead.
- Noisy loggers pinned to WARNING: `httpx`, `httpcore`, `urllib3`, `sqlalchemy.engine`, `asyncpg`,
  `litellm`, `uvicorn.access`.
- `redact_secrets` drops values for keys matching `api_key|password|secret|token|authorization|database_url`
  (case-insensitive) → `"[REDACTED]"`. Defence in depth; callers must still not log secrets.
- `version` = `APP_VERSION` env (the Docker build sets it to the git sha; default `dev`).

**Request context middleware** (`api/middleware.py`, pure ASGI, so it also wraps streaming responses):

1. Reads `X-Request-ID`; accepts it only if it matches `^[A-Za-z0-9._-]{1,128}$` (prevents log injection),
   otherwise generates a new UUID4 hex.
2. `clear_contextvars()` then `bind_contextvars(request_id=...)`.
3. Adds `X-Request-ID` to every response, including errors.
4. Emits `http_request_completed` (INFO) with `method`, `path`, `status_code`, `duration_ms`. `/healthz` and
   `/readyz` log at DEBUG to keep probe noise out.
5. Catches any unhandled exception: logs `request_failed` (ERROR, `exc_info`) and, if the response hasn't
   started, sends the 500 envelope. This lives in the middleware rather than a FastAPI `Exception` handler
   because Starlette runs that handler outside user middleware (no `X-Request-ID` header) and re-raises
   afterwards (a duplicate traceback line from uvicorn).

New events added to the catalog (observability.md §1.3): `app_started`, `app_stopped`, `http_request_completed`,
`readiness_check_failed`.

### 3.6 Errors

- `core/errors.py`: `SamvidhanError(Exception)` with class attrs `code: str`, `http_status: int`, and a
  user-safe `message`. Subclasses in this phase: `NotFoundError` (`NOT_FOUND`, 404), `ValidationFailedError`
  (`VALIDATION_ERROR`, 422), `ServiceUnavailableError` (`SERVICE_UNAVAILABLE`, 503).
- `api/errors.py` registers handlers that all return the envelope
  `{"error": {"code", "message", "request_id"}}`:
  `SamvidhanError` → its status; `RequestValidationError` → 422 `VALIDATION_ERROR`;
  Starlette `HTTPException` → its status with `NOT_FOUND` / `METHOD_NOT_ALLOWED` / `RATE_LIMITED` or
  `HTTP_<status>`; any other `Exception` → 500 `INTERNAL_ERROR` via the middleware (§3.5 step 5) with a generic
  message.
- Handlers don't log; the raise site logs with context and the middleware logs the final status.

### 3.7 API endpoints (this phase)

| Method | Path | Behaviour |
|--------|------|-----------|
| GET | `/healthz` | Always `200 {"status": "ok"}` — process is alive. No I/O. |
| GET | `/readyz` | `SELECT 1` with `READINESS_TIMEOUT_S`. `200 {"status": "ready", "checks": {"database": "ok"}}` or `503` envelope `SERVICE_UNAVAILABLE` with `readiness_check_failed` WARNING. Model / active-document checks are added in Phase 1/3. |

`api/main.py` exposes `create_app(settings: Settings | None = None) -> FastAPI`; run with
`uvicorn --factory samvidhan.api.main:create_app` (no module-level app, so importing has no side effects).
CORS allow-list from `CORS_ORIGINS`; `X-Request-ID` is an allowed and exposed header.

### 3.8 Makefile

| Target | Does |
|--------|------|
| `help` (default) | Lists targets |
| `setup` | `uv sync`; copies `.env.example` → `.env` if missing; installs pre-commit hooks if configured |
| `up` / `down` | Start `db` (wait for healthy) / stop stack (**never** `-v`) |
| `db-shell` / `db-logs` | `psql` in the container / follow DB logs |
| `migrate` / `migrate-down` / `migration m="..."` | Alembic upgrade head / downgrade -1 / autogenerate |
| `run` | uvicorn with reload on :8000 |
| `ui` | Streamlit (no-op message until Phase 6) |
| `ingest` | Ingestion CLI (available from Phase 1) |
| `lint` / `fmt` / `typecheck` | ruff check + format check + mypy / ruff format + fix / mypy |
| `test` / `test-unit` / `test-integration` | pytest all / `-m unit` / `-m integration` |
| `eval-retrieval` / `eval-router` / `eval-full` | eval suites (available from Phase 2/4/7) |
| `clean` | Remove caches (never data or volumes) |

## 4. Config

New keys (added to `.env.example`):

| Key | Default | Notes |
|-----|---------|-------|
| `SERVICE_NAME` | `samvidhan-api` | `service` log field |
| `APP_VERSION` | `dev` | git sha in Docker builds |
| `DB_HOST_PORT` | `5433` | Compose host port only |
| `DB_POOL_SIZE` | `5` | |
| `DB_MAX_OVERFLOW` | `5` | |
| `DB_CONNECT_TIMEOUT_S` | `5` | |
| `READINESS_TIMEOUT_S` | `2` | |

`DATABASE_URL` default changes to port 5433.

## 5. Failure modes

| Failure | Behaviour |
|---------|-----------|
| DB down at startup | App still starts (engine is lazy); `/healthz` 200, `/readyz` 503. No crash loop. |
| DB drops mid-run | `pool_pre_ping` recycles dead connections; requests fail with 503/500 envelope and log `request_failed`. |
| Port 5433 taken | `make up` fails with Docker's bind error; set `DB_HOST_PORT` in `.env`. |
| Bad `X-Request-ID` | Ignored; a fresh id is generated. |
| Invalid `.env` value | `Settings` raises at startup with the field name (fail fast). |

## 6. Acceptance criteria

- [x] `make setup && make up && make migrate` works on a clean clone while another project's Postgres holds 5432.
- [x] `docker compose` never touches containers/volumes outside the `samvidhan` project.
- [x] Migration 001 upgrades and downgrades cleanly (integration test).
- [x] `/healthz` returns 200; `/readyz` returns 200 with the DB up and 503 envelope with it down.
- [x] Every log line during a request — including SQLAlchemy/uvicorn lines — carries the same `request_id`
      (unit test captures output).
- [x] Response always has `X-Request-ID`; a valid incoming one is echoed, an invalid one replaced.
- [x] Unhandled exceptions return the 500 envelope without leaking the exception text.
- [x] Secret-looking keys are redacted in log output.
- [x] `make lint` (ruff + mypy strict) and `make test` pass.

## 7. Test plan

- Unit: config loads defaults / env overrides; logging JSON shape and standard fields; redaction; request-id
  middleware (echo, replace, generate, contextvar binding and clearing); error envelope for each handler;
  `/healthz`; `/readyz` with a fake failing DB dependency.
- Integration (`testcontainers`, `pgvector/pgvector:pg16`): migration upgrade → tables, indexes, extension present →
  downgrade → tables gone; `/readyz` 200 against the real DB.

## 8. Open questions

- None blocking. `version` from git sha requires the Docker build arg; local runs show `dev`.
