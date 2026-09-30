# Contributing

## Before you code

1. Find the task in [`docs/plans/implementation-plan.md`](docs/plans/implementation-plan.md).
2. Read the spec it references. No spec → write one from [`docs/specs/_template.md`](docs/specs/_template.md) and get
   it approved first.
3. Architectural change (new store, new model role, new pipeline stage)? Add an ADR in [`docs/adr/`](docs/adr/README.md).

## Setup

```bash
make setup      # uv sync + pre-commit install
make up         # Postgres + pgvector
make migrate
```

## Branches and commits

- Branch from `main`: `feat/<scope>-<short>`, `fix/…`, `chore/…`, `docs/…`, `eval/…`.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat(retrieval): add RRF fusion`,
  `fix(router): fall back on invalid JSON`.
- Keep PRs small (one plan task where possible).

## Before opening a PR

```bash
make lint       # ruff + mypy
make test       # pytest (unit + integration)
make eval-retrieval   # required if you touched ingestion, retrieval, prompts or models
```

pre-commit runs ruff, mypy and a secret scan on every commit.

## Pull requests

- Fill in the PR template checklist.
- RAG-affecting changes include the eval diff table (no gate below threshold, no metric regressed > 2 pts).
- CI must be green; one CODEOWNERS approval required; squash-merge into `main`.

## Code standards

See [`docs/standards/engineering-standards.md`](docs/standards/engineering-standards.md) — typing, config, errors,
logging, DB, prompts, testing, security.

## Releases

Semantic versioning. Update `CHANGELOG.md`, run the full eval on the `test` split, tag `vX.Y.Z`.
