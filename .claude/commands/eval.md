---
description: Run the evaluation suites and compare against thresholds and the last baseline
argument-hint: [retrieval|router|full]
---

Run evaluation suite: **$ARGUMENTS** (if empty, use `retrieval`).

1. Run `uv run python -m eval.run --suite <suite> --split dev`.
2. Read the new report in `eval/reports/` and the baseline `eval/reports/baseline.json`.
3. Print a table: metric | baseline | now | threshold | status (PASS / FAIL / REGRESSED by > 2 pts).
4. For every failing golden case, show id, question, expected vs retrieved articles, and a one-line hypothesis.
   For more than 5 failures, hand off to the `eval-analyst` agent.
5. Do not change thresholds or golden answers to make things pass. If you believe a golden case is wrong, say so and
   ask me.
6. Only if I say "promote", copy the report to `baseline.json`.
