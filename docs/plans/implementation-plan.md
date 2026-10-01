# Implementation Plan — Samvidhan RAG v1

- **Source of truth for scope:** `docs/design/HLD.md`
- **Rule:** phases run in order. A phase is done only when its exit criteria pass. Tick boxes as tasks land.
- **Eval-first:** the golden set and retrieval eval (Phase 2) exist *before* retrieval is tuned (Phase 3), so every
  retrieval decision is measured.
- Rough effort assumes one developer, part-time days.

| Phase | Name | Effort | Depends on |
|-------|------|--------|------------|
| 0 | Project foundation | 1–2 d | — |
| 1 | Ingestion & chunking | 3–4 d | 0 |
| 2 | Golden set & retrieval eval harness | 2–3 d | 1 |
| 3 | Retrieval (hybrid + rerank + pinned lookup) | 3 d | 2 |
| 4 | LLM layer, router & answer generation | 3–4 d | 3 |
| 5 | API, sessions, chat history & memory | 3 d | 4 |
| 6 | Streamlit UI | 1–2 d | 5 |
| 7 | Full evaluation, load test, hardening → **v1 release** | 3 d | 6 |
| 8 | v1.1 Rolling LLM summary | 1–2 d | 7 |
| 9 | v2 Observability & monitoring | 3–4 d | 7 |

---

## Phase 0 — Project foundation

**Goal:** an empty but production-shaped repo that lints, type-checks, tests and runs.
**Spec:** `docs/specs/foundation.md` · observability.md §1.

- [x] P0.1 `uv init`; `pyproject.toml` with ruff, mypy (strict on `src/`), pytest config and markers
      (standards §1, §8)
- [x] P0.2 Package skeleton `src/samvidhan/{api,core,ingestion,retrieval,query,generation,memory,llm,db}` per
      CLAUDE.md
- [x] P0.3 `core/config.py` (pydantic-settings) + `.env.example` with every key from the HLD (models, k values,
      thresholds, windows, rate limits, flags)
- [x] P0.4 `core/logging.py` — structlog JSON/console, contextvars, request-id middleware (observability §1)
- [x] P0.5 `docker-compose.yml`: `db` (pgvector/pgvector:pg16, host port 5433), `api` (profile `app`); HF cache
      volume (HLD §18). `ui` service deferred to P6.6
- [x] P0.6 Alembic init + migration 001: `documents`, `chunks` (with HNSW + GIN indexes) (HLD §10)
- [x] P0.7 FastAPI app with `/healthz`, `/readyz`, error envelope + `SamvidhanError` (standards §3–4)
- [ ] P0.8 GitHub Actions: ruff → mypy → pytest (unit) on PR
- [x] P0.9 `.gitignore` (data/raw, .env, eval/.cache, model caches)
- [ ] P0.10 `.pre-commit-config.yaml`: ruff (lint + format), mypy, gitleaks (secret scan), end-of-file/trailing
      whitespace, check-yaml/json
- [x] P0.11 `Makefile` targets: `setup`, `up`, `down`, `migrate`, `ingest`, `run`, `ui`, `lint`, `test`,
      `eval-retrieval`, `eval-router`, `eval-full`
- [ ] P0.12 `SECURITY.md` (how to report issues) and `CHANGELOG.md` (Keep a Changelog format)
- [ ] P0.13 GitHub: branch protection on `main` (PR required, CI green, CODEOWNERS review, no force-push);
      fill in `.github/CODEOWNERS`
- [ ] P0.14 Complete the HLD before coding past Phase 0: reviewers/sign-off, capacity & cost estimate,
      privacy/DPDP Act note (what we store, retention, deletion), rollout & rollback plan, glossary

**Exit criteria:** `make setup && make up && make migrate` works; pre-commit passes on a clean clone; `/readyz` returns 200 when the DB is up;
CI is green; logs show `request_id` on every line.

---

## Phase 1 — Ingestion & chunking

**Goal:** the official PDF becomes validated, structure-aware chunks in Postgres.
**Spec:** `docs/specs/ingestion.md` · HLD §7 · ADR-0001, ADR-0008.

- [x] P1.0 Local models: `pymupdf` + `sentence-transformers` deps; `ingestion/models.py` + `make models`
      (bge-m3, no ONNX); `EMBED_BATCH_SIZE`/`EMBED_MAX_LENGTH` config; slow smoke test (spec §3.10)
- [x] P1.1 Official English PDF in `data/raw/constitution.pdf` — edition "as on 1st May, 2024" (spec §3.1)
- [x] P1.2 `ingestion/types.py` + `ingestion/extract.py` — PyMuPDF spans → rows rebuilt by vertical overlap,
      bold prefix, `{{fn:N}}` footnote tokens, table cells in `parts`; text-PDF check (spec §3.2–3.3)
- [x] P1.3 `ingestion/clean.py` — running header/context line/page number removal, body range (PREAMBLE →
      APPENDIX I), hyphenation, whitespace/quotes (spec §3.1–3.2)
- [x] P1.4 `ingestion/footnotes.py` — rule-line split, per-page numbering, marker stripping, spill to next page,
      `amendment_notes` parsing (spec §3.2, §3.6)
- [x] P1.5 `ingestion/segment.py` — state machine for Part / Chapter / group heading / Article / Schedule / List /
      Appendix; ≥ 15 snippet tests (spec §3.4, §7)
- [x] P1.6 `eval/fixtures/expected_articles.txt` — bootstrapped from the Contents pages by a script, reviewed by
      hand, committed (spec §3.11)
- [x] P1.7 `ingestion/chunk.py` — chunker v1 rules, clause split > 800 tokens, Seventh Schedule groups of 10,
      `embed_text` vs `text`, bge-m3 token counts; chunk config keys (spec §3.5–3.6, §4)
- [x] P1.8 `ingestion/validate.py` — missing/duplicate/unexpected Articles, token cap, histogram;
      **fail on any missing Article** (spec §3.11)
- [x] P1.9 `ingestion/embed.py` — `Embedder` protocol, `BgeM3Embedder` (normalized, batch 16, max_length 1024,
      device auto cuda/mps/cpu), `FakeEmbedder` (spec §3.7)
- [x] P1.10 Migration 002 (`appendix` chunk type + `appendix_no`) and `db/repositories/corpus.py` — find by key,
      insert document + chunks in one transaction, activate (spec §3.8)
- [x] P1.11 `ingestion/cli.py` — `ingest <pdf>` idempotent by (sha256, chunker_version, embed_model),
      `--dry-run`, `--activate`, `activate <id>`; writes `chunks.jsonl` + `ingestion_report.json`; log events
      (spec §3.9, §3.12)
- [x] P1.12 Integration test: ~10-Article fixture → `FakeEmbedder` → Postgres rows with correct metadata;
      re-run no-op; activate flips (spec §7)
- [ ] P1.13 Full ingest (done: 702 chunks, active) + manual spot check of 30 random Articles vs the PDF
      (`eval/reports/ingestion_check.md`); update HLD §7.3 chunk estimate with the real count

**Exit criteria:** full PDF ingests in ≤ 20 min on CPU; validation passes with 0 missing/duplicate Articles; spot
check ≥ 29/30 clean; chunk count and token histogram recorded in `ingestion_report.json`.

---

## Phase 2 — Golden set & retrieval eval harness

**Goal:** a measuring stick before any tuning.
**Spec:** evaluation.md §2, §3.1, §6.

- [ ] P2.1 Hand-write 60 golden cases across all categories incl. `long_query` (start from `eval/golden/*.sample.jsonl`)
      — **partial:** 67 cases drafted in `eval/golden/single_turn.jsonl` (golden v0.1, `question_refs` added); owner
      review pending; categories below §2.4 targets
- [ ] P2.2 Synthetic generation script (LLM from random chunks) → human review → +90 cases; tag `source`
- [ ] P2.3 20 multi-turn conversations (pronoun follow-ups, topic switches, clarify → answer, one 8+ turn chat)
- [ ] P2.4 Stratified 70/30 dev/test split; `eval/golden/VERSION` = 1.0
- [x] P2.5 `eval/metrics.py` — Recall@k, Hit@1, MRR@10, nDCG@5, candidate recall (unit-tested with toy data)
- [x] P2.6 `eval/run.py --suite retrieval` + JSON/Markdown report writer + threshold/baseline comparison
- [ ] P2.7 CI job: retrieval suite on PR against the cached fixture DB

**Exit criteria:** ≥ 150 single-turn + 20 multi-turn cases; `eval.run --suite retrieval` produces a report and a
non-zero exit code on gate failure.

---

## Phase 3 — Retrieval

**Goal:** hit the retrieval gates.
**Spec:** HLD §8.3.

- [x] P3.1 `retrieval/dense.py` — query embedding + HNSW cosine top-k (raw SQL in `retrieval/sql.py`)
- [x] P3.2 `retrieval/lexical.py` — `websearch_to_tsquery` (terms OR-ed) + `ts_rank_cd` top-k
- [x] P3.3 `retrieval/fusion.py` — RRF (k=60), unit-tested
- [x] P3.4 `retrieval/lookup.py` — pinned fetch by `article_no` / `schedule_no`; ref normalizer
      (`"Art. 21-A"` → `21A`) validated against the known list
- [x] P3.5 `retrieval/rerank.py` — bge-reranker-v2-m3, max_length 512, candidates 15 → top 5; skip-on-error
- [x] P3.6 `retrieval/service.py` — orchestrates pinned + dense + lexical + fusion + rerank; returns a trace
      (ids, per-leg ranks, scores, top_score, low_confidence)
- [x] P3.7 Ablation run recorded in `eval/reports/ablation_v1.md`: dense only / lexical only / hybrid / hybrid +
      rerank
- [x] P3.8 Tune `LOW_CONFIDENCE_THRESHOLD` on dev (pick the value that best separates hits from misses)
- [ ] P3.9 Promote the first retrieval baseline (awaiting owner "promote"; dev run: Recall@5 0.931, MRR@10 0.931, Hit@1 lookup
      1.00 via pinning (0.50 unpinned), candidate recall@15 0.875 ✗, rerank p95 ~4–5 s ✗ — see
      `eval/reports/ablation_v1.md`)

**Exit criteria:** Recall@5 ≥ 0.90, Hit@1 (lookup) = 1.00, MRR@10 ≥ 0.75, candidate recall@15 ≥ 0.95 on dev;
retrieval p95 ≤ 300 ms and rerank p95 ≤ 800 ms locally.

---

## Phase 4 — LLM layer, router & answer generation

**Goal:** grounded, cited answers through a deterministic pipeline.
**Spec:** `docs/specs/llm-router-generation.md` · HLD §6, §8.2, §8.5, §8.6 · observability §1.5.

- [x] P4.1 Migration 003: `llm_calls`
- [x] P4.2 `llm/client.py` — LiteLLM wrapper (`complete`, `stream`), timeouts, 1 retry, provider fallback,
      TTFT, `llm_calls` insert, `llm_call_completed` / `llm_fallback` events; `FakeLLM` for tests
- [x] P4.3 `llm/budget.py` — daily per-model counter from `llm_calls` vs configured caps; `budget_near_cap`
- [x] P4.4 `prompts/router.v1.md` + `query/router.py` — `RouteDecision` Pydantic model, JSON mode, fallback to
      `simple`
- [x] P4.5 `query/hyde.py` and `query/decompose.py` (sub-query retrieval + merge, cap 8)
- [x] P4.6 `prompts/answer.v1.md` + `generation/answer.py` — excerpt formatting, streaming, omitted-Article
      handling, disclaimer
- [x] P4.7 `generation/citations.py` — extract, validate against retrieved refs (incl. `Appendix I–III`), drop
      invalid, `invalid_citation`
- [x] P4.8 Templated replies for `ambiguous`, `out_of_scope`, `chitchat`
- [x] P4.9 `eval/run.py --suite router` — type accuracy, refs F1, JSON validity, answer_style (standalone checks
      come in Phase 5 with memory). Golden v0.2 relabels ST-057–063 (`expected_type` was the category).
      **Dev run pending:** needs `GROQ_API_KEY` in `.env` (`make eval-router`)
- [x] P4.10 `graph/` — `ChatState`, thin nodes, `StateGraph` with conditional edges on `route.type`
      (HLD §8.1.1, ADR-0011); unit tests per node + one compiled-graph test with `FakeLLM`
- [x] P4.11 Export the graph to `docs/design/graph.mmd` (Mermaid) via a small script
- [ ] P4.12 Long-query mode (ADR-0012): `answer_style` in `RouteDecision`, 70B router above `LONG_QUERY_CHARS`,
      issue spotting, per-sub-query rerank, long context/answer limits, Gemini for long answers, truncation note;
      enforce every limit in HLD §13.2 from config — **partial:** `answer_style`, 70B router, `MAX_SUB_QUERIES_LONG`,
      issue-spotting prompt rule, per-style answer model / `max_tokens` / timeout and the truncation note are in;
      `MAX_CONTEXT_*_LONG` and per-sub-query rerank budgets for long answers are not

**Exit criteria:** router type accuracy ≥ 0.90, refs F1 ≥ 0.95, JSON validity ≥ 0.99 on dev; every LLM call
produces an `llm_calls` row (integration test); a provider failure falls back cleanly (test with a bad key).

---

## Phase 5 — API, sessions, chat history & memory

**Goal:** a conversational API that meets the HLD contract.
**Spec:** `docs/specs/api-sessions-memory.md` · HLD §8.1, §8.4, §9, §11.

- [x] P5.1 Migration 004: `chat_sessions`, `chat_messages`, `feedback`
- [x] P5.2 Repositories for sessions/messages/feedback
- [x] P5.3 `POST /v1/sessions`, `GET /v1/sessions/{id}/messages`, `DELETE /v1/sessions/{id}`
- [x] P5.4 `memory/loader.py` — summary + structured memory + last 6 messages
- [x] P5.5 `memory/structured.py` — update `last_articles`, `articles_discussed` (15), `parts_discussed`,
      `recent_topics` (5) after each answer
- [x] P5.6 `POST /v1/chat` — runs the LangGraph graph (HLD §8.1–8.1.1), maps graph stream events to SSE
      `meta/token/citations/done/error`, and
      `stream=false` JSON mode; persist route, trace, latency breakdown, prompt version
- [x] P5.7 `POST /v1/messages/{id}/feedback`, `GET /v1/articles/{no}`, `GET /v1/meta`
- [x] P5.8 Rate limiting (`limits`, see spec api-sessions-memory §3.6): per session, per IP, sessions/IP/day; message length limit
- [ ] P5.9 Session expiry job (30 days) + feedback anonymization; CLI `samvidhan.ops.cleanup`
- [ ] P5.10 Multi-turn eval: standalone correctness via `eval.run --suite router` over `multi_turn.jsonl`
- [ ] P5.11 Log completeness test: one request emits every stage event with the same `request_id`
- [ ] P5.12 Limit tests from evaluation.md §7.1 (length, empty, long-query switch, caps, truncation, rate,
      concurrency, timeout); `limit_applied` / `answer_truncated` / `input_rejected` events

**Exit criteria:** multi-turn standalone correctness ≥ 0.90; API contract tests pass; OpenAPI snapshot committed;
first token p95 ≤ 2.5 s over 20 local sample requests.

---

## Phase 6 — Streamlit UI

**Goal:** a demo-ready chat UI.
**Spec:** HLD §12.

- [ ] P6.1 Chat thread with SSE streaming; session in `st.session_state` + query param
- [ ] P6.2 Citation chips → expander with full Article text
- [ ] P6.3 👍/👎 + optional comment
- [ ] P6.4 New chat / clear; starter questions; header with disclaimer and edition date
- [ ] P6.5 Debug panel behind `DEBUG_UI` (route JSON, chunks + scores, latency breakdown)
- [ ] P6.6 Add `ui` service to docker compose

**Exit criteria:** full demo flow works via `docker compose up`: ask → stream → citations → follow-up → feedback.

---

## Phase 7 — Full evaluation, load test, hardening → v1 release

**Goal:** prove quality and performance, then tag v1.0.
**Spec:** evaluation.md §4–7 · HLD §14, §17.

- [ ] P7.1 `eval/ragas_runner.py` with pinned ragas, Gemini judge, cache, `purpose='eval_judge'` logging;
      save per-question scores as `<run_id>_samples.csv`
- [ ] P7.2 Custom metrics: refusal accuracy, false refusal, citation validity, expected citation rate, injection
      resistance, truncation rate, word-limit adherence
- [ ] P7.3 `eval.run --suite full` on dev → fix → re-run (use the `eval-analyst` agent for failures)
- [ ] P7.4 Nightly CI workflow for the full suite on `main`
- [ ] P7.5 Locust scenarios (mocked LLM + short real-LLM run); compare with SLOs
- [ ] P7.6 Chaos-lite checks (bad LLM key, DB down)
- [ ] P7.7 Security pass: CORS, input sanitization, `pip-audit`, no secrets in logs (grep test)
- [ ] P7.8 Run the full suite on **test** split; promote baseline; write `eval/reports/v1_release.md`
- [ ] P7.9 README: setup, architecture diagram (incl. the LangGraph Mermaid graph), eval results table, limitations
- [ ] P7.10 Streamlit **Eval** page: latest scores vs thresholds, trend across runs, failing questions drill-down
      (reads `eval/reports/`)
- [ ] P7.11 Runbooks in `docs/runbooks/`: LLM provider down / quota hit, re-ingest a new PDF edition, roll back a
      prompt or model, restore the database
- [ ] P7.12 Tag `v1.0.0`; update `CHANGELOG.md`

**Exit criteria:** every gate in evaluation.md §5 passes on the test split; SLOs in HLD §14 met; README complete.

---

## Phase 8 — v1.1 Rolling LLM summary

**Spec:** HLD §9.4.

- [ ] P8.1 Migration: `summary`, `summarized_upto_message_id`, `summary_version` (if not already in 004)
- [ ] P8.2 `prompts/summary.v1.md` + `memory/summarizer.py` (batch fold, ≤ 150 words)
- [ ] P8.3 BackgroundTask trigger + per-session lock (`FOR UPDATE SKIP LOCKED`)
- [ ] P8.4 Loader uses summary + `RAW_WINDOW=12`
- [ ] P8.5 Add 5 long (12+ turn) multi-turn golden conversations that reference early turns
- [ ] P8.6 Compare multi-turn metrics with `SUMMARY_ENABLED` on/off; enable only if equal or better

**Exit criteria:** long-conversation standalone correctness ≥ 0.90 with the flag on; no latency impact on the
request path.

---

## Phase 9 — v2 Observability & monitoring

**Spec:** observability.md §3.

- [ ] P9.1 Langfuse self-host in compose; LiteLLM callback + manual spans; `request_id` = trace id
- [ ] P9.2 Feedback → Langfuse scores; nightly RAGAS → scores/gauges
- [ ] P9.3 `/metrics` with Prometheus instrumentator + custom LLM counters/histograms
- [ ] P9.4 Prometheus + Grafana in compose; 5 dashboards (usage by day, latency, reliability, quality, traffic)
- [ ] P9.5 Alerts: quota 80%, TTFT p95, LLM error rate, invalid citations, quality drift → webhook
- [ ] P9.6 `samvidhan.ops.usage --days N` CLI (can land earlier; it only needs `llm_calls`)

**Exit criteria:** one request is visible end-to-end as a trace; "LLM usage by day" dashboard live; each alert
fired once in a test.

---

## Definition of Done (every task)

- Code + tests merged; ruff, mypy, pytest green.
- RAG-affecting change: `/eval` run, no gate failed, no metric regressed > 2 pts (diff in PR).
- New config keys added to `.env.example`; spec updated if behaviour changed.
- New LLM call paths go through `llm/client.py` (logged).
- Plan checkbox ticked.
