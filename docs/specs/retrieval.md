# Spec: Retrieval (hybrid + rerank + pinned lookup)

- **Status:** Approved by owner 2026-10-01
- **Owner:** Shubham
- **Related:** HLD §8.3, §13.2, §14 · ADR-0002 (hybrid + rerank), ADR-0004 (router extracts refs), ADR-0008
  (local models) · evaluation.md §2–3, §6 · observability.md §1.3 · plan Phase 3 (P3.1–P3.9), P2.5–P2.6
- **Last updated:** 2026-10-01 (P3.7–P3.8 results)

## 1. Problem / goal

Given a standalone query (and, optionally, Article/Schedule refs the router extracted), return the few chunks of the
active Constitution document that answer it, with enough of a trace to explain *why* each chunk was chosen. Dense
search handles plain-language questions ("can police arrest me without reason"); lexical search handles exact
tokens ("21A", "Tenth Schedule"); a cross-encoder reranker orders the merged candidates; explicitly named refs are
fetched by metadata and always included. Quality is measured, not guessed: the retrieval eval reports Recall@5,
MRR@10, Hit@1, nDCG@5 and candidate recall@15 on the dev split.

## 2. Scope

- **In scope:** dense leg (bge-m3 + pgvector HNSW), lexical leg (Postgres FTS), RRF fusion, pinned lookup by ref,
  ref normalizer + validation against the active document, bge-reranker-v2-m3, `RetrievalService` with a trace,
  low-confidence flag, a `search` CLI for manual checks, the retrieval eval suite (metrics, runner, reports,
  ablation, threshold tuning), ~60 hand-drafted golden cases.
- **Out of scope:** router / condense / HyDE / decomposition (Phase 4 — `long_query` and `multi_part` recall will
  improve then); API endpoints (Phase 5); bge-m3 sparse vectors (ADR-0002 "revisit"); agent fallback (v2);
  synthetic golden cases, multi-turn cases, CI job (P2.2–P2.4, P2.7).

## 3. Design

### 3.1 Data flow

```
query, refs ─┬─► normalize + validate refs ─► fetch_pinned ──────────────────────────┐
             ├─► embed (bge-m3) ─► dense top DENSE_K ─┐                              │
             └─► lexical top LEXICAL_K ───────────────┴► RRF top RERANK_CANDIDATES   │
                                                          │                          │
                                                          ▼                          ▼
                                             rerank → top FINAL_K ─► context = pinned + reranked,
                                                                      dedupe, cap MAX_CONTEXT_CHUNKS
```

Dense and lexical run concurrently, each on its own DB session. Retrieval is **stateless**: it never sees chat
history (AGENTS.md rule 3).

### 3.2 Modules (`src/samvidhan/retrieval/`)

| Module | Interface |
|--------|-----------|
| `types.py` | `ScoredChunk`, `RetrievalResult`, `RetrievalMode` (frozen dataclasses / Literal) |
| `sql.py` | The only raw SQL (bound parameters): `DENSE`, `LEXICAL`, `LOOKUP`, `KNOWN_REFS`. Every query is restricted to the active document |
| `dense.py` | `async dense_search(session, query_vec: Sequence[float], k: int) -> list[ScoredChunk]` |
| `lexical.py` | `async lexical_search(session, query: str, k: int) -> list[ScoredChunk]` |
| `fusion.py` | `rrf(legs: Mapping[str, Sequence[ScoredChunk]], k: int, limit: int) -> list[ScoredChunk]` (pure) |
| `lookup.py` | `normalize_ref(raw: str) -> str \| None` (pure); `async load_known_refs(session) -> frozenset[str]`; `async fetch_pinned(session, refs: Sequence[str]) -> list[ScoredChunk]` |
| `rerank.py` | `Reranker` protocol `score(query, texts) -> list[float]`; `BgeReranker`; `FakeReranker` |
| `service.py` | `RetrievalService(session_factory, embedder, reranker, settings)`; `async retrieve(query, *, refs=(), mode="hybrid_rerank") -> RetrievalResult` |
| `cli.py` | `python -m samvidhan.retrieval.cli search "<query>" [--refs 21A,SCH-7] [--mode …]` |

`ScoredChunk`: `id, chunk_type, article_no, schedule_no, appendix_no, article_title, text, embed_text, score,
ranks: dict[str, int]` (1-based per leg: `dense`, `lexical`, `rrf`, `rerank`), `scores: dict[str, float]`,
`pinned: bool`.

`RetrievalResult`: `chunks` (final context), `candidates` (post-fusion, pre-rerank — used for candidate recall),
`top_score` (best rerank score, `None` if rerank skipped/off), `low_confidence`, `refs` (validated), `trace`,
`latency_ms` (`embed`, `dense`, `lexical`, `pinned`, `fusion`, `rerank`, `total`).

`RetrievalMode`: `dense` | `lexical` | `hybrid` (RRF, no rerank) | `hybrid_rerank` (default). Used by the
ablation; production always uses `hybrid_rerank`.

### 3.3 Dense leg

Query embedded with the same `Embedder` used at ingestion (`ingestion/embed.py`, normalized, no instruction prefix
— bge-m3 does not use one), off the event loop via `asyncio.to_thread`. SQL orders by `embedding <=> :q`
(cosine distance, HNSW `vector_cosine_ops`); score = `1 - distance`. The query filters on the active `document_id` and sets
`hnsw.iterative_scan = strict_order` (pgvector ≥ 0.8) for its transaction: HNSW filters after the index scan, so
once an inactive document shares the index a plain scan could return fewer than `DENSE_K` active rows.

### 3.4 Lexical leg

`websearch_to_tsquery('english', q)` **ANDs** every term, so a natural-language question ("who appoints the chief
election commissioner") rarely matches any single chunk. The lexical leg therefore uses **OR semantics** built from
the same parser: `replace(websearch_to_tsquery('english', :q)::text, ' & ', ' | ')::tsquery`. Stop words and
stemming stay Postgres's `english` config; quoted phrases keep their `<->` operators. Ranked by
`ts_rank_cd(tsv, query)`. An empty query (all stop words) returns no rows. Websearch exclusions (`-word`) are stripped
before parsing: OR-ed, `!word` would match nearly every chunk. The ablation records AND vs OR once;
HLD §8.3 step 3 is updated to match what ships.

### 3.5 Fusion

`score(c) = Σ_legs 1 / (RRF_K + rank_leg(c))`, rank 1-based, `RRF_K=60`. Each leg's rank and score are carried in
`ranks` / `scores`. Ties break by best single-leg rank, then chunk id (deterministic). Output top
`RERANK_CANDIDATES`.

### 3.6 Refs: normalization, validation, pinned fetch

Canonical ids (shared with evaluation.md §2.2): Articles `21`, `21A`, `243ZO`; Schedules `SCH-1` … `SCH-12`;
Preamble `PREAMBLE`; Appendices `APP-I` … `APP-III`.

`normalize_ref` accepts (case-insensitive, surrounding whitespace/punctuation ignored):

| Input | Output |
|-------|--------|
| `21`, `Art. 21`, `Article 21`, `article21`, `A21`, `Art 21.` | `21` |
| `21-A`, `21 A`, `21a`, `Art. 21-A`, `243-I`, `243Z-O` | `21A`, `21A`, `21A`, `21A`, `243I`, `243ZO` |
| `SCH-7`, `Schedule 7`, `Schedule VII`, `Seventh Schedule`, `7th Schedule` | `SCH-7` |
| `Preamble` | `PREAMBLE` |
| `Appendix II`, `APP-2` | `APP-II` |
| anything else (`Section 302`, `21(1)(a)` → `21`; `foo` → `None`) | clause suffixes are stripped; unparsable → `None` |

Validation: normalized ids are checked against `load_known_refs` (distinct `article_no` / `schedule_no` /
`appendix_no` of the active document, cached on the service after first load). Unknown ids are dropped and logged
(`unknown_ref_dropped`, WARNING). At most `MAX_ARTICLE_REFS` are kept; extra ones → `limit_applied`
(`limit=article_refs`).

`fetch_pinned` returns **every** chunk of each ref (all sub-chunks of a split Article, all chunks of a Schedule),
in reading order (`seq`). Seventh Schedule has ~30 chunks; pinned chunks still count toward `MAX_CONTEXT_CHUNKS`,
so a Schedule ref is truncated to the cap with `limit_applied` (`limit=context_chunks`).

### 3.7 Rerank

`BgeReranker` wraps sentence-transformers `CrossEncoder(RERANK_MODEL, max_length=RERANK_MAX_LENGTH,
local_files_only=True)` and returns sigmoid scores in [0, 1] over `(query, embed_text)` pairs, off the event loop.
A missing model fails at startup with `InvalidSourceError("… run make models RERANK=1")`. Any exception during
scoring → log `rerank_skipped` (WARNING, `error`), keep RRF order, `top_score=None` (ADR-0008 fallback).

Local model calls (`BgeM3Embedder.embed`, `BgeReranker.score`) are serialised process-wide by `MODEL_LOCK`
(`ingestion/embed.py`) on every device: PyTorch's MPS backend is not thread-safe and concurrent forward passes
segfault the worker. Concurrent retrievals (decomposition, parallel requests) queue on the lock, so the `embed` and
`rerank` latencies include lock wait, and each waiting call holds a default-executor thread.

### 3.8 Context assembly and confidence

`chunks = dedupe(pinned + reranked[:FINAL_K])[:MAX_CONTEXT_CHUNKS]` — pinned first in reading order, then
reranked by score. If anything is trimmed → `limit_applied` (`limit=context_chunks`, `requested`, `allowed`).

`low_confidence = (no chunks at all) or (top_score is not None and top_score < LOW_CONFIDENCE_THRESHOLD and no
pinned chunks)` — an empty context is always weak, so the answer step says nothing relevant was found (AGENTS.md
rule 2). When true,
log `low_confidence_retrieval` (WARNING, `top_score`, `threshold`, `standalone_query` truncated to 200 chars).
`LOW_CONFIDENCE_THRESHOLD` is tuned on dev (§3.10): **0.05**, the best-accuracy split on golden v0.1 dev
(0.054, accuracy 0.88 over 33 cases; hits median 0.86, misses/out-of-scope median 0.04). bge-reranker sigmoid scores
are low for paraphrased questions (e.g. 0.26 for a correct Art 22 hit), so the pre-tuning default of 0.30 flagged
correct answers. Re-tune when the golden set reaches v1.0.

### 3.9 Trace (stored on the assistant message in Phase 5)

```json
{
  "mode": "hybrid_rerank",
  "refs": ["21A"],
  "pinned": ["art-21A#0"],
  "candidates": [{"id": "art-21#0", "ranks": {"dense": 1, "lexical": 3, "rrf": 1}, "scores": {"dense": 0.71}}],
  "final": [{"id": "art-21A#0", "pinned": true}, {"id": "art-21#0", "rerank": 0.93}],
  "top_score": 0.93,
  "low_confidence": false,
  "latency_ms": {"embed": 35, "dense": 8, "lexical": 4, "rerank": 210, "total": 260}
}
```

### 3.10 Eval harness (P2.5, P2.6, P3.7–P3.9)

- `eval/golden.py` — Pydantic `GoldenCase` (evaluation.md §2.2 + `question_refs`); `load_cases(path, split)`;
  every ref must be canonical.
- `eval/metrics.py` — pure metric functions (evaluation.md §3.1), `chunk_ref(chunk)` maps a chunk to its canonical
  ref. A ref counts once however many of its sub-chunks appear.
- `eval/run.py --suite retrieval --split dev [--mode M] [--ablation]`:
  - Each case calls `RetrievalService.retrieve(question, refs=question_refs)`.
  - Scored cases: non-empty `expected_refs`, `must_refuse=false`. `long_query` cases are reported in their own
    section and are not part of the gated aggregate until decomposition lands (P4.5).
  - Metrics run over the served order: pinned chunks, then all reranked candidates (the first `FINAL_K` of which
    are the context). Recall@5, Hit@1 (`article_lookup`), nDCG@5 and MRR@10 use that order; candidate recall@15 uses
    `candidates` only (before rerank, no pins), so a miss there is a retrieval miss, not a rerank miss.
  - Pinning a `question_refs` ref makes lookup metrics 1.0 by construction (a perfect router), so every report
    also gives `recall@5_unpinned`, `mrr@10_unpinned`, `hit@1_unpinned` and `hit@1_lookup_unpinned`, computed
    on the search legs alone (they never see refs). These show retrieval quality when the router misses a ref.
  - Reports record `git describe --always --dirty`, so a run on uncommitted code is marked as such. Latency p50/p95 for retrieval (embed + legs + fusion) and
    rerank.
  - Output: `eval/reports/<ts>_retrieval_<split>.{json,md}` + `_samples.csv`; gate check vs evaluation.md §5 and vs
    `baseline.json` (> 2 pt regression); non-zero exit on gate failure.
  - **Confidence section** (P3.8): positives = in-scope cases whose top-1 chunk is an expected ref; negatives =
    `out_of_scope` cases + top-1 misses. Reports the `top_score` threshold with the best accuracy and both
    distributions.
  - `--ablation` runs all four modes (plus lexical AND vs OR) and writes `eval/reports/ablation_v1.md`.
- Baseline promotion only on the owner's "promote" (`.claude/commands/eval.md`).

### 3.11 Log events

| Event | Level | Fields |
|-------|-------|--------|
| `retrieval_completed` | INFO | `mode`, `n_pinned`, `n_dense`, `n_lexical`, `n_candidates`, `duration_ms` |
| `rerank_completed` | INFO | `top_score`, `final_ids`, `duration_ms` |
| `rerank_skipped` | WARNING | `error` |
| `low_confidence_retrieval` | WARNING | `top_score`, `threshold`, `standalone_query` (≤ 200 chars) |
| `unknown_ref_dropped` | WARNING | `ref` |
| `limit_applied` | WARNING | `limit`, `requested`, `allowed` |

## 4. Config

No new keys. Uses existing settings: `EMBED_MODEL`, `RERANK_MODEL`, `MODEL_DEVICE`, `EMBED_BATCH_SIZE`,
`EMBED_MAX_LENGTH`, `DENSE_K=20`, `LEXICAL_K=20`, `RRF_K=60`, `RERANK_CANDIDATES=15`, `RERANK_MAX_LENGTH=512`,
`FINAL_K=10`, `MAX_CONTEXT_CHUNKS=12`, `MAX_ARTICLE_REFS=10`, `LOW_CONFIDENCE_THRESHOLD=0.05` (tuned on dev, §3.8).

## 5. Failure modes

| Failure | Behaviour |
|---------|-----------|
| No active document | `ServiceUnavailableError` ("no active corpus; run ingest --activate") |
| Reranker model missing | Startup fails with a pointer to `make models RERANK=1` |
| Reranker error at query time | `rerank_skipped` (with `exc_info`); RRF order; `top_score=None`; `low_confidence=false` unless the context is empty |
| Unknown / unparsable ref | Dropped, `unknown_ref_dropped`; retrieval continues |
| Lexical query empty after parsing | Lexical leg returns `[]`; dense alone feeds fusion |
| DB error | Propagates (API maps to 503 in Phase 5) |

## 6. Acceptance criteria

- [ ] `python -m samvidhan.retrieval.cli search` prints hybrid results with per-leg ranks for sample queries.
- [ ] Ref normalizer passes the §3.6 table (unit tests).
- [ ] RRF is deterministic and matches hand-computed scores (unit tests).
- [ ] Integration: dense, lexical, pinned and full `retrieve()` work against Postgres with `FakeEmbedder`; inactive
      documents are never returned.
- [ ] `eval.run --suite retrieval --split dev` writes JSON + Markdown reports and exits non-zero on gate failure.
- [ ] Ablation recorded in `eval/reports/ablation_v1.md`.
- [ ] `LOW_CONFIDENCE_THRESHOLD` tuned on dev and recorded here.
- [ ] Eval (dev): Recall@5 ≥ 0.90, Hit@1 (lookup) = 1.00, MRR@10 ≥ 0.75, candidate recall@15 ≥ 0.95; retrieval
      p95 ≤ 300 ms, rerank p95 ≤ 800 ms. Gaps are reported, not hidden.

## 7. Test plan

- **Unit:** `test_lookup.py` (normalizer table), `test_lexical.py` (exclusion stripping), `test_fusion.py`,
  `test_service.py` (incl. rerank skip-on-error, ref validation and caps) (fakes: pinned first, dedupe, cap + `limit_applied`, low confidence, modes),
  `test_eval_metrics.py` (toy data per metric).
- **Integration:** `test_retrieval_db.py` (testcontainers, fixture corpus, `FakeEmbedder`, `FakeReranker`).
- **Slow:** `test_rerank_model.py` (real model smoke; skips if not cached).
- **Eval:** retrieval suite on dev; ablation.

## 8. Open questions

- ~~Lexical OR vs AND~~ — decided: OR (Recall@5 0.625 vs 0.458, `eval/reports/ablation_v1.md`); HLD §8.3 updated.
- **Keep the lexical leg?** On golden v0.1 dev, dense + rerank beats hybrid + rerank on every metric, including
  unpinned lookups (0.70 vs 0.50), and has the best candidate recall@15 (0.972 vs 0.875). This is ADR-0002's
  revisit trigger and an owner decision.
- **Rerank latency:** p95 ~4.3 s on Apple-Silicon MPS vs the 800 ms SLO (ADR-0008 revisit trigger). Options:
  fewer candidates, `RERANK_MAX_LENGTH=256` (−3 pts Recall@5, ~2× faster), a smaller reranker, or rerank only
  when the top RRF results disagree.
- Pinned Schedules larger than the context cap (Seventh Schedule): v1 truncates in reading order. That can drop a
  later ref entirely (`[SCH-7, SCH-11]`) and every reranked chunk. Phase 4 should consider at least one chunk per
  ref (round-robin), or pin only the reranked chunks of a large Schedule, if eval shows a need.
- `known_refs` is cached per process; activating a new document needs an API restart (fine for v1's blue/green).
