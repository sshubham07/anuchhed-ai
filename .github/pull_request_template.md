## What & why

<!-- One or two sentences. -->

**Plan task:** P?.?  ·  **Spec/ADR:** <!-- link -->

## Checklist

- [ ] Spec exists and is up to date (or not needed: _reason_)
- [ ] Tests added/updated; `make lint` and `make test` pass
- [ ] New config keys added to `.env.example`
- [ ] New LLM calls go through `llm/client.py` (logged to `llm_calls`)
- [ ] Migration is reversible (if any)
- [ ] No secrets, full prompts or raw IPs logged
- [ ] Plan checkbox ticked

## Eval (required for ingestion / retrieval / prompt / model changes)

| Metric | Baseline | This PR | Threshold | Status |
|--------|----------|---------|-----------|--------|
| Recall@5 | | | ≥ 0.90 | |
| MRR@10 | | | ≥ 0.75 | |
| … | | | | |

## Screenshots / notes
