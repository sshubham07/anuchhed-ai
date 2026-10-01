# Spec: Evaluation & Testing

- **Status:** Draft v1.0
- **Related:** HLD §14, §15 · plan Phase 2, Phase 4–5 (router), Phase 7 (full eval)
- **Last updated:** 2026-10-01

The point of this spec: every change that can affect answer quality is measured the same way, against the same
data, with fixed pass/fail thresholds.

---

## 1. Evaluation layers

```
                    ┌──────────────────────────┐
                    │ 5. Human review (weekly) │  👎 feedback + 20 random answers
                  ┌─┴──────────────────────────┴─┐
                  │ 4. RAGAS end-to-end (nightly) │  faithfulness, relevancy, context P/R
                ┌─┴──────────────────────────────┴─┐
                │ 3. Router eval (prompt changes)   │  type acc, refs F1, standalone query
              ┌─┴──────────────────────────────────┴─┐
              │ 2. Retrieval eval (every PR, no LLM)  │  Recall@k, MRR, nDCG
            ┌─┴──────────────────────────────────────┴─┐
            │ 1. pytest unit + integration (every commit)│
            └────────────────────────────────────────────┘
```

Cheap and deterministic at the bottom (run constantly); expensive and LLM-judged at the top (run less often).

---

## 2. Golden set

### 2.1 Files

```
eval/golden/
  single_turn.jsonl          # ~150 cases
  multi_turn.jsonl           # ~20 conversations (~60 turns)
  single_turn.sample.jsonl   # format examples (shipped with these specs)
  multi_turn.sample.jsonl
eval/fixtures/
  expected_refs.txt    # canonical list of Article ids for ingestion validation
```

### 2.2 Single-turn schema

```json
{
  "id": "ST-001",
  "split": "dev",
  "category": "simple",
  "difficulty": "easy",
  "question": "Who appoints the Chief Election Commissioner?",
  "expected_type": "simple",
  "expected_refs": ["324"],
  "acceptable_refs": [],
  "reference_answer": "The Chief Election Commissioner is appointed by the President under Article 324(2) ...",
  "must_refuse": false,
  "expected_answer_style": "brief",
  "notes": ""
}
```

| Field | Meaning |
|-------|---------|
| `split` | `dev` (use freely while tuning) or `test` (held out; only for releases) — ~70/30 |
| `category` | Coverage bucket (§2.4) |
| `expected_type` | Router label — one of the seven router types (validated on load; golden v0.2) |
| `expected_refs` | **Must** appear in the retrieved top-k (recall is computed on these). Canonical ids: Articles `21`, `21A`; Schedules `SCH-7`; Preamble `PREAMBLE` |
| `acceptable_refs` | Also relevant; not required, not penalized |
| `question_refs` | Refs the question names explicitly (canonical ids), e.g. `["21A"]` for "explain art. 21-A". Optional; defaults to `[]`. The retrieval suite passes them as pinned refs (a perfect router); the router suite scores `article_refs` F1 against them |
| `reference_answer` | Short ground truth written from the Constitution text; used by RAGAS context recall/precision |
| `must_refuse` | True for out-of-scope questions |
| `expected_answer_style` | `brief` / `detailed` / `exam` (router label, HLD §8.5). Optional; defaults to `brief` |
| `expected_word_limit` | Word limit the user asked for (e.g. 250 for "answer in 250 words"). Optional; used by word-limit adherence |

### 2.3 Multi-turn schema

```json
{
  "id": "MT-001",
  "split": "dev",
  "turns": [
    {"user": "What does Article 21 say?", "expected_type": "article_lookup", "expected_refs": ["21"]},
    {"user": "What are its exceptions?", "expected_type": "simple",
     "expected_standalone_contains": ["21"], "expected_refs": ["21"], "acceptable_refs": ["359"]}
  ]
}
```

Multi-turn turns may also carry `expected_answer_style` (e.g. a follow-up asking for a 250-word exam answer).

Turns are replayed in order through the real API (`stream=false`) with a fresh session. Each turn is scored like a
single-turn case, plus a check that `standalone_query` contains the expected tokens and does **not** contain
`expected_standalone_excludes` (used for topic switches).

### 2.4 Coverage (target counts)

| Category | Count | Purpose |
|----------|-------|---------|
| `article_lookup` | 20 | Exact refs incl. lettered (21A, 51A), "Art.", spelled-out numbers |
| `simple` (plain-language) | 40 | Vocabulary gap: "can police arrest me without reason" → Art 22 |
| `multi_part` | 15 | Comparisons, "X and Y" |
| `conceptual` | 15 | Broad themes: minorities, federalism, emergency |
| `schedule` | 10 | Seventh Schedule lists, Tenth (anti-defection), Eighth (languages) |
| `omitted_amended` | 10 | Art 31 (omitted), 21A / 51A (inserted), amendment-note questions |
| `out_of_scope` | 20 | BNS/IPC, case law, current office holders, other countries, general knowledge |
| `ambiguous` | 5 | "What are my rights?" |
| `chitchat` | 5 | Greetings, thanks |
| `adversarial` | 10 | Prompt injection ("ignore your rules and…"), false premises ("Article 21 guarantees free Wi-Fi, right?") |
| `long_query` | 15 | UPSC mains-style analytical questions ("Discuss the federal features… with reference to…") and advocate fact scenarios (some over 500 chars, up to 4,000, to exercise the 70B router); checks issue spotting, `answer_style` and coverage of every expected Article. Recall for these cases is measured on the full final context (up to `MAX_CONTEXT_CHUNKS_LONG`), not top-5. Until decomposition lands (P4.5) they are reported in their own section of the retrieval report and left out of the gated aggregate |
| **Multi-turn** | 20 convos | Pronoun follow-ups, topic switches, clarify → answer, 6+ turn conversations for memory |

### 2.5 How the golden set is built

1. **Hand-write 60** cases across all categories (the most valuable ones — real phrasing, traps).
2. **Synthesize ~90** with an LLM from random chunks ("write a question a citizen might ask that this Article
   answers"). Each generated case is **reviewed by a human** — fix or drop. Tag with `"source": "synthetic"`.
3. **Mine production:** every 👎 and every `low_confidence_retrieval` is reviewed weekly; good ones become new cases.
4. Split 70/30 dev/test, stratified by category. **Never tune on `test`.**
5. Version the set (`eval/golden/VERSION`). Changing an existing case needs a note in the PR. The version stays `0.x` while the set is below the §2.4 target counts; `1.0` marks the full set.

---

## 3. Metrics

### 3.1 Retrieval (deterministic, no LLM cost)

Computed on the final top-k after rerank (k=5), and on pre-rerank candidates (k=15) to separate misses.

| Metric | Definition |
|--------|------------|
| Recall@5 | Share of `expected_refs` present in the top-5 (averaged per case) |
| Hit@1 (article_lookup) | Top chunk is the referenced Article |
| MRR@10 | Mean of 1/rank of the first expected Article |
| nDCG@5 | Graded relevance: expected = 2, acceptable = 1 |
| Candidate recall@15 | Recall before rerank (diagnoses rerank vs retrieval problems) |

Ref-level matching: a chunk counts for ref `21` if `chunk.article_no == "21"` (any sub-chunk), and for `SCH-7` if
`chunk.schedule_no == "7"`.

### 3.2 Router

| Metric | Definition |
|--------|------------|
| Type accuracy | `route.type == expected_type` (confusion matrix in the report) |
| article_refs F1 | Set F1 between extracted refs and the refs in the question |
| Standalone correctness | Multi-turn: contains all `expected_standalone_contains`, none of `…_excludes` |
| JSON validity | Share of router outputs that parse without fallback |

### 3.3 Generation (RAGAS + custom)

| Metric | Tool | What it catches |
|--------|------|-----------------|
| Faithfulness | RAGAS `Faithfulness` | Claims not supported by retrieved context (hallucination) |
| Response relevancy | RAGAS `ResponseRelevancy` | Answer drifts from the question |
| Context precision | RAGAS `LLMContextPrecisionWithReference` | Irrelevant chunks ranked high |
| Context recall | RAGAS `LLMContextRecall` | Reference-answer facts missing from context |
| Refusal accuracy | custom | `must_refuse` cases correctly refused |
| False refusal rate | custom | In-scope cases wrongly refused |
| Citation validity | custom | Share of raw citations that were in the retrieved set (before the validator drops them) |
| Expected citation rate | custom | Answer cites at least one `expected_article` |
| Injection resistance | custom | Adversarial cases keep rules (judge: yes/no) |
| Truncation rate | custom | Share of answers that hit the answer token cap (`finish_reason=length`) — too-small limits or rambling answers |
| Word-limit adherence | custom | Cases with `expected_word_limit`: answer word count within ±20% of the limit (e.g. 200–300 for 250) |
| Limit compliance | custom | No case exceeds any HLD §13.2 limit silently: every trim (sub-queries, refs, context) is logged as `limit_applied` |

---

## 4. RAGAS setup

- **Pin the exact `ragas` version** in `pyproject.toml`; its API changes between minor versions. Adapt the snippet
  below to the pinned version.
- **Judge LLM:** Gemini Flash (a different family from the Llama generator), `temperature=0`. Fallback judge:
  `groq/openai/gpt-oss-120b`.
- **Judge embeddings** (for ResponseRelevancy): the same bge-m3 used in the app.
- Judge calls are logged to `llm_calls` with `purpose='eval_judge'` so eval cost/quota is visible.
- Run with limited concurrency (≤ 4) to respect free-tier RPM; cache judge results keyed by
  (case_id, answer_hash, metric, judge_model) in `eval/.cache/` so re-runs are cheap.

```python
# eval/ragas_runner.py (shape only — match the pinned ragas API)
from ragas import EvaluationDataset, evaluate
from ragas.metrics import (Faithfulness, ResponseRelevancy,
                           LLMContextPrecisionWithReference, LLMContextRecall)

samples = [{
    "user_input": case.question,                 # or the standalone_query for multi-turn turns
    "retrieved_contexts": [c.text for c in result.chunks],
    "response": result.answer,
    "reference": case.reference_answer,
} for case, result in zip(cases, results) if not case.must_refuse]

report = evaluate(
    dataset=EvaluationDataset.from_list(samples),
    metrics=[Faithfulness(), ResponseRelevancy(),
             LLMContextPrecisionWithReference(), LLMContextRecall()],
    llm=judge_llm, embeddings=judge_embeddings,
)
```

Refusal, citation and injection metrics are computed by our own code (they don't fit RAGAS's schema).

---

## 5. Thresholds (quality gates)

Initial targets; revisit after the first baseline. A PR **fails** if a gated metric is below threshold **or**
regresses by more than 2 points versus `baseline.json`.

| Metric | Threshold | Gate |
|--------|-----------|------|
| Recall@5 | ≥ 0.90 | Every PR |
| Hit@1 (article_lookup) | = 1.00 | Every PR |
| MRR@10 | ≥ 0.75 | Every PR |
| Candidate recall@15 | ≥ 0.95 | Every PR |
| Router type accuracy | ≥ 0.90 | Router/prompt PRs |
| article_refs F1 | ≥ 0.95 | Router/prompt PRs |
| Standalone correctness (multi-turn) | ≥ 0.90 | Router/prompt/memory PRs |
| Router JSON validity | ≥ 0.99 | Router/prompt PRs |
| Faithfulness | ≥ 0.85 | Nightly / release |
| Response relevancy | ≥ 0.80 | Nightly / release |
| Context precision | ≥ 0.75 | Nightly / release |
| Context recall | ≥ 0.85 | Nightly / release |
| Refusal accuracy | ≥ 0.95 | Nightly / release |
| False refusal rate | ≤ 0.05 | Nightly / release |
| Citation validity (raw) | ≥ 0.98 | Nightly / release |
| Expected citation rate | ≥ 0.90 | Nightly / release |
| Injection resistance | = 1.00 | Nightly / release |
| Long-query ref coverage (share of `expected_refs` cited, `long_query` cases) | ≥ 0.80 | Nightly / release |
| Truncation rate | ≤ 0.05 | Nightly / release |
| Word-limit adherence | ≥ 0.90 | Nightly / release |
| `answer_style` accuracy | ≥ 0.85 | Router/prompt PRs |

Release rule: all gates pass on the **test** split.

---

## 6. Runner and reports

```bash
uv run python -m eval.run --suite retrieval --split dev     # ~seconds, no LLM
uv run python -m eval.run --suite router    --split dev     # router LLM only
uv run python -m eval.run --suite full      --split test    # router + answer + RAGAS judge
```

- The retrieval suite calls the retrieval module directly using each case's `question` (single-turn) as the
  standalone query, so it stays LLM-free. Router quality is measured separately.
- The full suite calls the running API (`stream=false`) exactly like a user would.
- Output: `eval/reports/<timestamp>_<suite>_<split>.json` + a Markdown summary:

```json
{
  "run_id": "2026-10-05T10-12-00_full_test",
  "git_sha": "abc123",
  "config": {"chunker": "v1", "embed_model": "BAAI/bge-m3", "answer_model": "groq/llama-3.3-70b-versatile",
             "router_prompt": "router.v1", "answer_prompt": "answer.v1", "judge": "gemini-flash"},
  "golden_version": "1.0",
  "metrics": {"recall@5": 0.93, "mrr@10": 0.81, "faithfulness": 0.88, "...": "..."},
  "by_category": {"simple": {"recall@5": 0.91}, "...": {}},
  "failures": [{"id": "ST-044", "metric": "recall@5", "expected": ["22"], "got": ["21","20","19"]}]
}
```

- Per-question scores (every metric for every case) are saved as `<run_id>_samples.csv` for debugging.
- The Streamlit **Eval** page (plan P7.10) shows the latest run vs thresholds, the trend across runs, and failing
  questions.
- `eval/reports/baseline.json` is the last promoted retrieval report and `baseline_router.json` the last promoted
  router report (see `/eval`).
- The router suite calls the real router model (every call logged to `llm_calls`), paced by `--rpm` (default 25)
  for free-tier limits, with an empty memory per case. Details: `docs/specs/llm-router-generation.md` §3.11.
- CI: the retrieval suite runs on every PR against a pre-built fixture DB (ingested once per chunker/embedding
  version and cached). The full suite runs nightly on `main` and posts a summary as a workflow artifact.

---

## 7. Other test types

| Type | Scope | Notes |
|------|-------|-------|
| Unit | Segmenter (Part/Chapter/Article regexes on tricky pages), chunk splitting, ref normalizer ("Art. 21-A" → `21A`), RRF math, citation extractor/validator, memory updates, SSE event order, router fallback | FakeLLM, no network |
| Integration | 10-Article fixture corpus → ingest → hybrid search → `/v1/chat` with FakeLLM; Alembic upgrade/downgrade; rate limiter; session expiry job | testcontainers `pgvector/pgvector:pg16` |
| Contract | API schemas snapshot-tested (OpenAPI diff in CI) | |
| Ingestion validation | Expected Article list, duplicates, size histogram, 30-Article manual spot check on first ingest | CLI report |
| Load | Locust: 10 → 30 users, 60% simple / 20% lookup / 10% multi-part / 10% follow-up. Run (a) with a mocked LLM to test pipeline capacity, (b) a short real-LLM run for TTFT | Compare with HLD §14 |
| Chaos-lite | Kill LLM provider (bad key) → fallback works; DB down → 503 + readyz red | Manual before release |
| Human review | Weekly: all 👎 + 20 random answers, scored for correctness, citation, tone | Findings → golden set |
| Limits | Boundary tests for every HLD §13.2 limit (§7.1) | Integration, FakeLLM, every PR |

### 7.1 Limit tests (integration, run on every PR)

Each test reads the limit from config (never a literal), so changing a limit doesn't break tests.

| Test | Input | Expected |
|------|-------|----------|
| Max length accepted | message of exactly `MAX_MESSAGE_CHARS` (4,000) chars | 200; pipeline runs |
| Over length rejected | `MAX_MESSAGE_CHARS + 1` chars | 422 `MESSAGE_TOO_LONG`, message states the limit; **no LLM call** (`llm_calls` unchanged) |
| Empty / whitespace | `""`, `"   \n"` | 422 `EMPTY_MESSAGE`; no LLM call |
| Long-query switch | `LONG_QUERY_CHARS` and `LONG_QUERY_CHARS + 1` chars | Router model = `ROUTER_MODEL` vs `ROUTER_MODEL_LONG` (checked in `llm_calls.model`) |
| Sub-query cap | FakeLLM router returns 8 sub-queries on a long query | 5 searches run (`MAX_SUB_QUERIES_LONG`); `limit_applied` logged with `limit=sub_queries` |
| Article-ref cap | Question naming 14 Articles | 10 pinned (`MAX_ARTICLE_REFS`); answer notes skipped refs; `limit_applied` logged |
| Context cap | Retrieval returns more than the cap | Context ≤ `MAX_CONTEXT_CHUNKS[_LONG]` and ≤ `MAX_CONTEXT_TOKENS[_LONG]`; pinned chunks kept |
| Answer cap | FakeLLM returns `finish_reason=length` | Answer ends with the "shortened — ask me to continue" note; `answer_truncated` logged |
| Session message cap | Session at `MAX_MESSAGES_PER_SESSION` | Next message → "start a new chat" response; no LLM call |
| Rate limit | `RATE_LIMIT_SESSION` + 1 requests in a minute | 429 `RATE_LIMITED` with `Retry-After` |
| Concurrency cap | `MAX_CONCURRENT_STREAMS` + 1 open streams | 503 `BUSY` for the extra one |
| Timeout | FakeLLM sleeps past `LLM_TIMEOUT_S` | 1 retry → fallback model → `LLM_UNAVAILABLE` if both fail |

---

## 8. Acceptance criteria for this spec

- [ ] `single_turn.jsonl` ≥ 150 cases and `multi_turn.jsonl` ≥ 20 conversations, all categories at target counts,
      70/30 split.
- [ ] Retrieval suite runs in CI in < 2 min without network access to LLM providers.
- [ ] Full suite produces a JSON + Markdown report and exits non-zero on gate failure.
- [ ] Judge calls are cached and logged to `llm_calls`.
- [ ] Every limit test in §7.1 passes; truncation rate and word-limit adherence are reported in every full run.
- [ ] First baseline promoted and all gates pass on `test`.
