# Spec: API, sessions, chat history & memory

- **Status:** Implemented (P5.1–P5.8; owner approved the Phase 5 plan on 2026-10-01)
- **Owner:** Shubham
- **Related:** HLD §8.1, §8.4, §9, §10, §11, §13.2 · ADR-0006, ADR-0007, ADR-0011 · observability.md §1.3 ·
  plan Phase 5 (P5.1–P5.8)
- **Last updated:** 2026-10-01

## 1. Problem / goal

The Phase 4 pipeline only runs from the CLI with an in-process session store. This spec turns it into the
conversational HTTP API of HLD §11: anonymous sessions and messages in Postgres, structured memory that lets
follow-ups resolve ("what are its exceptions?" → Article 21 via `last_articles`), `POST /v1/chat` streaming the
LangGraph pipeline as Server-Sent Events, feedback / article / meta endpoints, and rate limits.

**Done when:** a follow-up resolves its Article through `last_articles` loaded from Postgres, and answers stream
token by token over `curl -N`.

## 2. Scope

- **In scope:** migration 004 (`chat_sessions`, `chat_messages`, `feedback`); repositories; `memory/loader.py` and a
  Postgres `MemoryStore`; `parts_discussed`; `/v1/sessions*`, `/v1/chat` (SSE + JSON), `/v1/messages/{id}/feedback`,
  `/v1/articles/{no}`, `/v1/meta`; readiness checks for models and active document; rate limiting and message
  validation (P5.1–P5.8).
- **Out of scope:** session expiry job and feedback anonymization CLI (P5.9); multi-turn eval (P5.10); log
  completeness test (P5.11); the full limit test suite and `MAX_CONCURRENT_STREAMS` (P5.12); rolling summary
  (Phase 8; `summary` is read if present but never written).

## 3. Design

### 3.1 Tables (migration 004)

Exactly HLD §10 `chat_sessions`, `chat_messages`, `feedback`. Extra index `chat_sessions(client_ip_hash,
created_at)` for the sessions-per-IP-per-day count. Messages cascade on session delete; `feedback.message_id` is
`ON DELETE SET NULL` and the row keeps its `question` / `answer` snapshot.

### 3.2 Repositories (`db/repositories/chat.py`)

Take an `AsyncSession`; the caller commits.

- `SessionRepository`: `create`, `get`, `delete`, `count_created_since(ip_hash, since)`,
  `update_memory(id, memory)` (also sets `last_active_at = now()`), `count_messages`.
- `MessageRepository`: `add_user`, `add_assistant`, `page(session_id, before, limit)` (oldest first; also
  the memory loader's history), `get`, `previous_user(message)`.
- `FeedbackRepository.add(message, rating, comment)` snapshots the preceding user message and the answer.
- `CorpusRepository.parts_for_articles(article_nos)`: distinct `part_no` of the active document's chunks for
  the given Article numbers; `CorpusRepository.active_chunks_for_ref(ref)` for `/v1/articles`.

Session ids are UUIDv7 (`uuid_utils.uuid7`).

### 3.3 Memory flow

1. API persists the user message (HLD §8.1 step C) and puts its id in `ChatState.user_message_id`.
2. `load_memory` → `PostgresSessionStore.load(session_id, before_message_id)`: `summary` + `memory` jsonb from the
   session row, plus the last `ROUTER_HISTORY_MESSAGES` messages with `id < user_message_id` (so the current
   question is not in its own history).
3. `save_turn` → `PostgresSessionStore.save_turn(session_id, turn)` in one transaction: insert the assistant row
   (route, standalone query, cited refs, retrieval trace, prompt version, latency), apply `apply_turn` to the
   structured memory, write it back. Returns `SavedTurn(memory, message_id)`.

Structured memory jsonb (HLD §9.3): `last_articles` (cited refs of the last answer; kept when nothing was cited),
`articles_discussed` (latest 15), `parts_discussed` (latest 15, Parts of cited Articles), `recent_topics` (last 5
standalone queries of non-template routes). Retrieval never sees any of it (AGENTS rule 3).

### 3.4 Endpoints

| Method | Path | Success | Errors |
|--------|------|---------|--------|
| POST | `/v1/sessions` | 201 `{session_id}` | 429 sessions/IP/day |
| GET | `/v1/sessions/{id}/messages?limit=50&before=<id>` | 200 `{messages, next_before}` — ascending ids; `next_before` is the oldest id when more exist | 404 |
| DELETE | `/v1/sessions/{id}` | 204 | 404 |
| POST | `/v1/chat` `{session_id, message, stream=true}` | SSE or 200 JSON (HLD §11) | 404 session, 409 `SESSION_FULL`, 422 `EMPTY_MESSAGE` / `MESSAGE_TOO_LONG`, 429, 503 |
| POST | `/v1/messages/{id}/feedback` `{rating: 1 or -1, comment?≤1000}` | 201 `{feedback_id}` | 404 (unknown or not an assistant message) |
| GET | `/v1/articles/{ref}` | 200 `{ref, label, title, part_no, part_title, is_omitted, text, chunk_ids}` | 404 |
| GET | `/v1/meta` | 200 edition date, chunker/embed/rerank versions, router/answer models, prompt versions, app version | — |

`/v1/articles/{ref}` accepts anything `normalize_ref` accepts (`21A`, `21-A`, `Sch. 7`, `preamble`).

### 3.5 SSE contract (`POST /v1/chat`, `stream=true`)

`text/event-stream`, headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Each event is
`event: <name>\ndata: <json>\n\n`:

| Event | When | Data |
|-------|------|------|
| `meta` | route decided | `{request_id, session_id, route_type, standalone_query, refs, answer_style}` |
| `token` | each piece of answer text | `{text}` — LLM tokens, templated replies, "not covered" reply, the disclaimer |
| `citations` | after citation validation; empty for templated / "not covered" replies | `{citations: [{ref, label, title}], invalid}` |
| `done` | graph end | `{message_id, answer, low_confidence, latency_ms}` — `answer` is the stored final text |
| `error` | any failure after the stream started | `{code, message, request_id}`; the stream ends |

Every user-visible text is emitted as `token` by the graph nodes (custom stream), so concatenated tokens equal the
final answer except where invalid citations were removed — clients should replace with `done.answer`.
`stream=false` runs the same generator and returns `{message_id, answer, citations, route, low_confidence,
latency_ms}`.

Latency (`chat_messages.latency_ms`, `done.latency_ms`): one entry per graph node, `ttft` (answer LLM) when an LLM
answered, and `total` (graph start → save).

### 3.6 Rate limits and input checks

`api/ratelimit.py` wraps the `limits` library (`limits.aio`, moving window, in-memory storage, one instance). We use
`limits` directly instead of slowapi decorators because the per-session key lives in the JSON body, which slowapi's
key function cannot read.

| Scope | Key | Limit |
|-------|-----|-------|
| chat per IP | `ip:<hash>` | `RATE_LIMIT_IP`, `RATE_LIMIT_IP_DAILY` |
| chat per session | `session:<id>` | `RATE_LIMIT_SESSION` |
| feedback per IP | `ip:<hash>` | `RATE_LIMIT_IP`, `RATE_LIMIT_IP_DAILY` |
| sessions per IP | counted in `chat_sessions` since UTC midnight | `SESSIONS_PER_IP_DAILY` |

Hit → `rate_limited` log (`scope`), HTTP 429 `RATE_LIMITED` with `Retry-After` (seconds). IP hash =
`sha256(ip + IP_HASH_SALT)`; raw IPs are never stored or logged, and settings refuse the default salt outside
`ENV=dev`. The client IP is the TCP peer, or the first `X-Forwarded-For` entry when the peer is listed in
`TRUSTED_PROXY_IPS` — the Streamlit UI calls the API from its server for every user, so it must forward the
user's IP or every per-IP limit becomes global (Phase 6 sets this up).

Message checks run before any LLM call: stripped empty → 422 `EMPTY_MESSAGE`; longer than `MAX_MESSAGE_CHARS` →
422 `MESSAGE_TOO_LONG` stating the limit; session at `MAX_MESSAGES_PER_SESSION` → 409 `SESSION_FULL` ("start a new
chat"). Each logs `input_rejected`.

### 3.7 App wiring

`create_app(settings, services_factory=None)`. The lifespan calls the factory (default: real engine, LiteLLM client,
retrieval service with local models, Postgres memory store, compiled graph, rate limiter) and stores `AppServices`
on `app.state`. Tests inject fakes. `/readyz` checks database, models loaded and an active document. Shutdown
drains the `llm_calls` recorder and disposes the engine. During `/v1/chat` the route binds `session_id` in log
contextvars so `llm_calls.session_id` is filled.

## 4. Config

New: `TRUSTED_PROXY_IPS` (comma-separated, default empty). Uses `MAX_MESSAGE_CHARS`, `MAX_MESSAGES_PER_SESSION`, `ROUTER_HISTORY_MESSAGES`, `RATE_LIMIT_SESSION`,
`RATE_LIMIT_IP`, `RATE_LIMIT_IP_DAILY`, `SESSIONS_PER_IP_DAILY`, `IP_HASH_SALT`.

## 5. Failure modes

| Failure | Behaviour |
|---------|-----------|
| LLM unavailable / busy mid-stream | `error` event with `LLM_UNAVAILABLE` / `BUSY`; the user message stays stored, no assistant row |
| Unexpected exception mid-stream | `error` event `INTERNAL_ERROR`, `request_failed` logged |
| Same error before streaming (`stream=false`) | Normal error envelope with its HTTP status |
| Client disconnects | The event generator is closed (`aclosing`), cancelling the graph; no assistant row unless the answer was already saved |
| Concurrent requests near a cap | Check-then-insert: `MAX_MESSAGES_PER_SESSION` / `SESSIONS_PER_IP_DAILY` may overshoot by a few (soft caps) |
| Session deleted between turns | 404 on the next chat |
| DB down | 503 from `/readyz`; chat returns 500 envelope |
| `.env` names a retired model | Provider `not_found` → fallback → `LLM_UNAVAILABLE` `error` event (copy the Models block from `.env.example`) |
| Rate-limit storage | In-process: resets on restart; per-instance only (Redis backend if > 1 replica, HLD §5) |

## 6. Acceptance criteria

- [x] Migration 004 upgrades and downgrades cleanly.
- [x] Session create / history page / delete (cascade) work; unknown ids → 404.
- [x] `POST /v1/chat` streams `meta → token… → citations → done`; templated routes stream `meta → token → citations` (empty) `→ done`.
- [x] Assistant rows store route, standalone query, cited refs, retrieval trace, prompt version and latency.
- [x] A follow-up's router prompt contains `last_articles` from the previous turn, loaded from Postgres.
- [x] `stream=false` returns the HLD §11 JSON shape.
- [x] Feedback snapshot survives session delete.
- [x] `/v1/articles/21-A` and `/v1/articles/21A` return the same Article; unknown → 404.
- [x] Rate limits return 429 with `Retry-After`; empty / too-long messages are rejected before any LLM call.
- [x] Eval: retrieval metrics unchanged (no retrieval code touched).

## 7. Test plan

- Unit: `apply_turn` (`parts_discussed`), request validation, rate limiter, SSE mapping, error event, JSON mode,
  graph nodes emitting template / disclaimer tokens.
- Integration (`tests/integration/test_chat_api.py`, testcontainers + FakeLLM + FakeEmbedder/FakeReranker): every
  acceptance criterion above.
- Manual: two-turn `curl -N` conversation with the real LLM.

## 8. Open questions

- First token p95 is ~4–5 s locally, dominated by rerank on CPU/MPS (retrieval eval: rerank p95 ~4.8–6.9 s).
  The Phase 5 exit criterion (≤ 2.5 s) depends on the open rerank-latency item from Phase 3 (P3.9).
