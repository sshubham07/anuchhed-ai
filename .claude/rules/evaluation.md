---
paths:
  - "eval/**"
---

# Evaluation rules

- Spec: `docs/specs/evaluation.md`.
- Never tune on the `test` split. Never change thresholds or golden answers to make a run pass — flag suspected
  golden errors instead.
- Golden-set changes bump `eval/golden/VERSION` and are explained in the PR.
- Judge calls go through `llm/client.py` with `purpose='eval_judge'` and are cached.
