# Retrieval ablation v1 (P3.7)

git `ea56ff5-dirty` · golden v0.1 · split `dev` · 36 gated cases (long_query excluded) · generated 2026-10-01

Hit@1 (lookup) is via pinning in every row; the unpinned column is the search legs alone.

| Variant | Recall@5 | MRR@10 | nDCG@5 | Hit@1 (lookup) | Hit@1 lookup unpinned | Recall@5 unpinned | Cand. recall@15 | retrieval p95 ms | rerank p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| dense only | 0.889 | 0.846 | 0.802 | 1.000 | 0.000 | 0.722 | 0.972 | 83 | — |
| lexical only (AND, HLD original) | 0.458 | 0.454 | 0.421 | 1.000 | 0.100 | 0.194 | 0.194 | 5 | — |
| lexical only (OR) | 0.625 | 0.563 | 0.516 | 1.000 | 0.200 | 0.417 | 0.486 | 30 | — |
| hybrid (RRF, OR) | 0.778 | 0.707 | 0.664 | 1.000 | 0.300 | 0.542 | 0.875 | 89 | — |
| hybrid + rerank | 0.931 | 0.931 | 0.877 | 1.000 | 0.500 | 0.875 | 0.875 | 105 | 4888 |

### Diagnostic variants (ad-hoc script, same dev cases and scoring, same day)

| Variant | Recall@5 | MRR@10 | nDCG@5 | Hit@1 (lookup) | Hit@1 lookup unpinned | Recall@5 unpinned | Cand. recall@15 |
|---|---|---|---|---|---|---|---|
| dense + rerank (no lexical leg) | 0.958 | 0.962 | 0.911 | 1.000 | 0.700 | 0.903 | 0.972 |
| hybrid + rerank, `RERANK_CANDIDATES=25` | 0.931 | 0.933 | 0.883 | 1.000 | 0.600 | 0.875 | 0.944 |

An earlier ad-hoc run with `RERANK_MAX_LENGTH=256` gave Recall@5 0.903 (−3 pts) at roughly half the rerank time.

## Findings

1. **Lexical OR beats AND** (Recall@5 0.625 vs 0.458). `websearch_to_tsquery`'s AND rarely matches a whole
   natural-language question, so OR semantics ship (retrieval spec §3.4; HLD §8.3 updated).
2. **The reranker carries quality.** Hybrid → hybrid + rerank: Recall@5 0.778 → 0.931, MRR 0.707 → 0.931.
3. **On this set the lexical leg hurts.**
   - Dense alone has candidate recall@15 of 0.972. RRF with the weaker lexical leg pushes refs out (0.875), and
     that is the failing gate.
   - Dense + rerank beats hybrid + rerank on every metric, including exact-number lookups without pinning
     (0.70 vs 0.50).
   - Caveat: only 36 cases.
   - This is ADR-0002's "revisit when" trigger. It is an owner decision and was not changed in this phase.
4. **Lookups depend on the router.**
   - With refs pinned (`question_refs`, standing in for a perfect router), lookup Hit@1 is 1.00 by construction.
   - With the search legs alone it is 0.50 (hybrid + rerank).
   - Router refs F1 (≥ 0.95, Phase 4) is therefore what really protects this gate (ADR-0004).
5. **Rerank latency misses the SLO.** p95 is about 4–5 s against 800 ms on Apple-Silicon MPS, for 15
   candidates of up to 512 tokens each. fp16 saves about 15%. This is ADR-0008's "revisit when" trigger.
6. **Gaps:**
   - ST-037 asks about the "supreme commander of armed forces", but Art 53 says "supreme command of the Defence
     Forces".
   - ST-006: 300A is crowded out by 31A–31D.
   - ST-060 retrieves Art 246 instead of the Seventh Schedule entry.
