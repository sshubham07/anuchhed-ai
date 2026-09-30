# Samvidhan RAG — developer commands (spec: foundation §3.8). `make` or `make help` lists targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose
PDF ?= data/raw/constitution.pdf
API_PORT ?= 8000

.PHONY: help setup up down db-shell db-logs migrate migrate-down migration run ui ingest \
        lint fmt typecheck test test-unit test-integration eval-retrieval eval-router eval-full clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## ---- Setup ----
setup: ## Install deps, create .env from the template, install git hooks
	uv sync
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — add your API keys")
	@if [ -f .pre-commit-config.yaml ]; then uv run pre-commit install; else echo "pre-commit: no config yet (P0.10)"; fi

## ---- Database (project-scoped compose; never touches other projects' containers) ----
up: ## Start Postgres + pgvector and wait until healthy
	$(COMPOSE) up -d --wait db
	@$(COMPOSE) ps db

down: ## Stop the stack (keeps the data volume)
	$(COMPOSE) down

db-shell: ## psql into the database
	$(COMPOSE) exec db psql -U samvidhan -d samvidhan

db-logs: ## Follow database logs
	$(COMPOSE) logs -f db

migrate: ## Apply all migrations
	uv run alembic upgrade head

migrate-down: ## Roll back the last migration
	uv run alembic downgrade -1

migration: ## Create a migration: make migration m="add llm_calls"
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	uv run alembic revision --autogenerate -m "$(m)"

## ---- Run ----
run: ## API with auto-reload on :8000
	uv run uvicorn --factory samvidhan.api.main:create_app --reload --port $(API_PORT) --no-access-log

ui: ## Streamlit UI on :8501 (Phase 6)
	@if [ -f ui/app.py ]; then uv run streamlit run ui/app.py; else echo "ui/app.py not implemented yet (Phase 6)"; fi

ingest: ## Ingest the Constitution PDF: make ingest PDF=path.pdf (Phase 1)
	uv run python -m samvidhan.ingestion.cli ingest $(PDF)

## ---- Quality ----
lint: ## ruff lint + format check + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

fmt: ## Auto-format and apply safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy (strict on src/)
	uv run mypy src

test: ## All tests (integration tests need Docker)
	uv run pytest

test-unit: ## Unit tests only (no Docker, no network)
	uv run pytest -m unit

test-integration: ## Integration tests (testcontainers Postgres)
	uv run pytest -m integration

## ---- Eval (docs/specs/evaluation.md) ----
eval-retrieval: ## Retrieval metrics, no LLM cost (Phase 2)
	uv run python -m eval.run --suite retrieval

eval-router: ## Router accuracy (Phase 4)
	uv run python -m eval.run --suite router

eval-full: ## Router + retrieval + RAGAS (Phase 7)
	uv run python -m eval.run --suite full

clean: ## Remove caches (never data or volumes)
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -r {} +
