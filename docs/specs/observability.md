# Spec: Logging, Observability & Monitoring

- **Status:** Draft v1.0
- **Related:** HLD §16, §10 (`llm_calls`), §21 · plan Phase 0 (logging core), Phase 4–5 (LLM + request logging), Phase 9 (monitoring)
- **Last updated:** 2026-09-30

Two stages:
- **Now (v1): structured logging + the `llm_calls` table.** This is enough to answer "what happened on request X?"
  and "how many tokens did we use per day?" with SQL.
- **Later (v2): tracing, metrics, dashboards and alerts** for LLM usage by day/model/purpose, latency, errors,
  quality signals.

---

## 1. Structured logging (v1)

### 1.1 Setup

- `structlog` configured in `core/logging.py`; std-lib `logging` (uvicorn, sqlalchemy, httpx, litellm) is routed
  through the same processors.
- Output: JSON lines to stdout in `staging`/`prod`; colored console in `dev` (`LOG_FORMAT=json|console`).
- Level via `LOG_LEVEL` (default INFO). Noisy libraries pinned to WARNING (`httpx`, `sqlalchemy.engine`, `litellm`).
- Context binding: middleware sets `request_id` (from the `X-Request-ID` header or a new UUID) and, once known,
  `session_id`, using `structlog.contextvars`. Background tasks re-bind the originating `request_id`.

### 1.2 Standard fields (every line)

| Field | Example |
|-------|---------|
| `timestamp` | ISO-8601 UTC |
| `level` | `info` |
| `event` | `retrieval_completed` |
| `service` | `samvidhan-api` |
| `env` | `dev` / `prod` |
| `version` | git sha |
| `request_id` | `01J…` |
| `session_id` | UUID (when known) |
| `duration_ms` | stage duration (stage events) |

### 1.3 Event catalog

| Event | Level | Extra fields |
|-------|-------|--------------|
| `chat_request_received` | INFO | `message_len`, `client_ip_hash` |
| `memory_loaded` | DEBUG | `n_messages`, `has_summary`, `last_articles` |
| `router_completed` | INFO | `route_type`, `article_refs`, `n_sub_queries`, `use_hyde`, `duration_ms` |
| `router_fallback` | WARNING | `reason` (`invalid_json` / `timeout`) |
| `retrieval_completed` | INFO | `n_pinned`, `n_dense`, `n_lexical`, `n_candidates`, `duration_ms` |
| `rerank_completed` | INFO | `top_score`, `final_ids`, `duration_ms` |
| `low_confidence_retrieval` | WARNING | `top_score`, `threshold`, `standalone_query` (truncated) |
| `llm_call_completed` | INFO | `purpose`, `provider`, `model`, `input_tokens`, `output_tokens`, `latency_ms`, `ttft_ms`, `status` |
| `llm_fallback` | WARNING | `purpose`, `from_model`, `to_model`, `error_code` |
| `invalid_citation` | WARNING | `cited`, `retrieved_refs` |
| `limit_applied` | WARNING | `limit` (`sub_queries` / `article_refs` / `context_chunks` / `context_tokens`), `requested`, `allowed` |
| `answer_truncated` | WARNING | `answer_style`, `max_tokens`, `model` |
| `input_rejected` | INFO | `reason` (`too_long` / `empty` / `session_full`), `length`, `limit` |
| `answer_completed` | INFO | `route_type`, `cited_refs`, `low_confidence`, `latency_breakdown` |
| `summary_updated` | INFO | `folded_messages`, `summary_len`, `duration_ms` |
| `budget_near_cap` | WARNING | `model`, `used_today`, `cap` |
| `rate_limited` | INFO | `scope` (`session` / `ip`) |
| `request_failed` | ERROR | `error_code`, `exc_info` |
| `ingestion_*` | INFO | `ingestion_started/completed/validation_failed` with counts |

Example line:

```json
{"timestamp":"2026-10-05T08:12:44.120Z","level":"info","event":"llm_call_completed","service":"samvidhan-api",
 "env":"prod","version":"abc123","request_id":"01JB6…","session_id":"0192…","purpose":"answer",
 "provider":"groq","model":"llama-3.3-70b-versatile","input_tokens":2140,"output_tokens":312,
 "latency_ms":1840,"ttft_ms":410,"status":"ok"}
```

### 1.4 Privacy and hygiene rules

- Never log: API keys, `DATABASE_URL`, full prompts, full answers, raw IPs.
- User message text: only at DEBUG, truncated to 200 chars. The full text lives in `chat_messages`.
- `standalone_query` may be logged truncated (≤ 200 chars) on WARNING events for debugging.
- Log retention (self-hosted): 14 days for app logs. `llm_calls` kept 180 days (aggregates forever).

### 1.5 Where LLM calls are recorded

A single wrapper `llm/client.py::complete()` / `stream()` around LiteLLM:
1. Starts a timer; records TTFT on the first streamed token.
2. On completion, emits `llm_call_completed` and inserts one `llm_calls` row (async, fire-and-forget with error
   logging). Tokens come from the provider usage field. Cost comes from LiteLLM's `completion_cost()` (0 for free
   tiers, but stays correct if we move to a paid model).
3. On fallback, the failed attempt is recorded with `status='error'`, and the successful one with
   `status='fallback'`.

**No code path may call an LLM without this wrapper** (reviewed by the `code-reviewer` agent).

---

## 2. Usage analytics with SQL (v1, no extra tools)

```sql
-- Requests, tokens and cost per day per model
SELECT date_trunc('day', created_at) AS day, model, purpose,
       count(*) AS calls,
       sum(input_tokens) AS in_tok, sum(output_tokens) AS out_tok,
       round(sum(cost_usd), 4) AS cost_usd
FROM llm_calls
GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;

-- Latency percentiles per day (answer calls)
SELECT date_trunc('day', created_at) AS day,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms)    AS ttft_p95
FROM llm_calls WHERE purpose = 'answer' GROUP BY 1 ORDER BY 1 DESC;

-- Fallback / error rate per day
SELECT date_trunc('day', created_at) AS day,
       avg((status <> 'ok')::int)::numeric(5,3) AS non_ok_rate
FROM llm_calls GROUP BY 1 ORDER BY 1 DESC;

-- Today's usage vs free-tier cap (used by the budget guard)
SELECT model, count(*) FROM llm_calls
WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') GROUP BY model;

-- Route mix and low-confidence rate per day
SELECT date_trunc('day', created_at) AS day, route->>'type' AS route_type, count(*),
       avg(((retrieval_trace->>'low_confidence')::boolean)::int) AS low_conf_rate
FROM chat_messages WHERE role = 'assistant' GROUP BY 1, 2 ORDER BY 1 DESC;

-- Feedback ratio per day
SELECT date_trunc('day', created_at) AS day,
       count(*) FILTER (WHERE rating = 1)  AS up,
       count(*) FILTER (WHERE rating = -1) AS down
FROM feedback GROUP BY 1 ORDER BY 1 DESC;
```

Provider free-tier caps generally reset on the provider's schedule (often UTC midnight or rolling windows). The
budget guard reads caps from config, not from these queries' assumptions.

A small CLI wraps these: `uv run python -m samvidhan.ops.usage --days 7` prints a daily table.

---

## 3. Observability stack (v2 — later phase)

### 3.1 Tracing

- **Langfuse** (self-hosted via Docker, open source) — or Arize Phoenix as an alternative.
- One trace per chat request, keyed by `request_id`, with `session_id` as the Langfuse session:

```
trace: chat_request (session_id, route_type, prompt_versions)
 ├─ span: memory_load
 ├─ generation: router        (model, tokens, latency, input/output JSON)
 ├─ generation: hyde          (optional)
 ├─ span: retrieval           (dense/lexical/rrf ids + scores)
 ├─ span: rerank              (top_score, final ids)
 ├─ generation: answer        (model, tokens, TTFT, latency)
 └─ span: persist
```

- User 👍/👎 is attached as a Langfuse score on the trace. Nightly RAGAS scores are attached to eval traces.
- Integration: LiteLLM's Langfuse callback for generations, plus manual spans via the Langfuse SDK / OpenTelemetry.
  The `llm_calls` table stays as the source of truth.

### 3.2 Metrics (Prometheus)

Exposed at `/metrics` (`prometheus-fastapi-instrumentator` + custom):

| Metric | Type | Labels |
|--------|------|--------|
| `http_request_duration_seconds` | histogram | route, status |
| `chat_stage_duration_seconds` | histogram | stage (router/retrieval/rerank/ttft/total) |
| `llm_requests_total` | counter | provider, model, purpose, status |
| `llm_tokens_total` | counter | model, purpose, direction (in/out) |
| `llm_cost_usd_total` | counter | model, purpose |
| `llm_latency_seconds` | histogram | model, purpose |
| `route_type_total` | counter | type |
| `low_confidence_total` | counter | — |
| `invalid_citation_total` | counter | — |
| `feedback_total` | counter | rating |
| `rate_limited_total` | counter | scope |

### 3.3 Dashboards (Grafana)

1. **LLM usage (by day):** calls, tokens in/out, cost — by model and purpose; % of free-tier cap used today.
2. **Latency:** p50/p95 per stage; TTFT; SLO lines from HLD §14.
3. **Reliability:** error and fallback rate by provider; 429s; router JSON fallbacks.
4. **Quality signals:** low-confidence rate, invalid-citation rate, 👍/👎 ratio, route mix, out-of-scope share;
   latest nightly RAGAS scores (pushed as gauges).
5. **Traffic:** requests/min, active sessions/day, messages per session.

### 3.4 Alerts

| Alert | Condition | Action |
|-------|-----------|--------|
| Quota | Model usage > 80% of daily cap | Check fallback; consider a paid tier |
| Latency | TTFT p95 > 2.5 s for 15 min | Check provider latency / reranker |
| Errors | Non-ok LLM rate > 5% for 10 min | Provider incident? Check fallback |
| Hallucinated citations | `invalid_citation` > 3% of answers per day | Review prompts; run eval |
| Quality drift | Nightly faithfulness < threshold or 👎 ratio > 20% (weekly) | Run eval-analyst; triage |

Delivery: email or Telegram/Slack webhook (single developer).

### 3.5 Acceptance criteria

- [ ] v1: every pipeline stage emits its event with `request_id`; `llm_calls` row for 100% of LLM calls (verified
      by an integration test with FakeLLM counting rows).
- [ ] v1: usage CLI prints the last 7 days by model/purpose.
- [ ] v2: a single request is fully visible as one Langfuse trace with all spans.
- [ ] v2: Grafana "LLM usage by day" dashboard live; quota and latency alerts tested.
