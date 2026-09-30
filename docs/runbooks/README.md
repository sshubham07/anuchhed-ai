# Runbooks

Step-by-step procedures for operating the service. Written during Phase 7 (plan task P7.11).

| Runbook | When to use |
|---------|-------------|
| `llm-provider-down.md` | Provider errors / quota exhausted; verify fallback, switch models via config |
| `reingest-new-edition.md` | A new edition of the Constitution PDF is published |
| `rollback-prompt-or-model.md` | A prompt or model change degrades quality in production |
| `restore-database.md` | Restore Postgres from backup |

Each runbook: symptoms → checks (logs/queries) → steps → verification → follow-ups.
