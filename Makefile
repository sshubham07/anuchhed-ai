# Samvidhan RAG — developer commands (spec: foundation §3.8). `make` or `make help` lists targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose
PDF ?= data/raw/constitution.pdf
API_PORT ?= 8000

.PHONY: help setup up down db-shell db-logs migrate migrate-down migration run ui models ingest \
        lint fmt typecheck openapi test test-unit test-integration test-llm eval-retrieval eval-router eval-full \
        clean diagrams graph ask chat hooks

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## ---- Setup ----
setup: ## Install deps, create .env from the template, install git hooks
	uv sync
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — add your API keys")
	@if [ -f .pre-commit-config.yaml ]; then uv run pre-commit install; else echo "pre-commit: no config yet (P0.10)"; fi
	@$(MAKE) --no-print-directory hooks

hooks: ## Install the git commit-msg hook that keeps README.md in step with changes
	@printf '#!/bin/sh\nexec .claude/hooks/readme-guard.sh --git "$$1"\n' > .git/hooks/commit-msg
	@chmod +x .git/hooks/commit-msg
	@echo "Installed .git/hooks/commit-msg (README guard)"

## ---- Database (project-scoped compose; never touches other projects' containers) ----
up: ## Start Postgres + pgvector and wait until healthy
	$(COMPOSE) up -d --wait db
	@$(COMPOSE) ps db

down: ## Stop the stack (keeps the data volume)
	$(COMPOSE) down

db-shell: ## psql into the database
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

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

models: ## Download local models (bge-m3 embeddings) into the HF cache; RERANK=1 adds the reranker
	uv run python -m samvidhan.ingestion.models download $(if $(RERANK),--rerank,)

ask: ## Ask the full pipeline: make ask Q="What does Article 21 say?" [ARGS="--fake-llm --debug"]
	@test -n "$(Q)" || (echo 'usage: make ask Q="your question"' && exit 1)
	uv run python -m samvidhan.graph.cli $(ARGS) ask "$(Q)"

chat: ## Interactive chat in the terminal (follow-ups keep context) [ARGS="--fake-llm"]
	uv run python -m samvidhan.graph.cli $(ARGS) chat

ingest: ## Ingest the PDF: make ingest [PDF=path.pdf] [ARGS="--activate" | ARGS="--dry-run"]
	uv run python -m samvidhan.ingestion.cli ingest $(PDF) $(ARGS)

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

test-llm: ## Live LLM tests (needs GROQ_API_KEY / GEMINI_API_KEY in .env)
	uv run pytest -m llm -rs

## ---- Eval (docs/specs/evaluation.md) ----
eval-retrieval: ## Retrieval metrics, no LLM cost (Phase 2)
	uv run python -m eval.run --suite retrieval --split dev

eval-router: ## Router accuracy on dev — real router LLM, every call logged (needs GROQ_API_KEY)
	uv run python -m eval.run --suite router --split dev

eval-full: ## Router + retrieval + RAGAS (Phase 7)
	uv run python -m eval.run --suite full

diagrams: ## Render README diagrams (docs/diagrams/*.mmd → .svg; needs Node)
	@for f in docs/diagrams/*.mmd; do \
		npx -y @mermaid-js/mermaid-cli -q -c docs/diagrams/mermaid.config.json -b white -i $$f -o $${f%.mmd}.svg; \
	done

openapi: ## Refresh the committed OpenAPI snapshot (docs/api/openapi.json)
	DATABASE_URL=postgresql+asyncpg://x:x@localhost:1/x uv run python -m samvidhan.api.openapi > docs/api/openapi.json

graph: ## Export the LangGraph pipeline to Mermaid (docs/design/graph.mmd) and re-render its SVG
	uv run python -m samvidhan.graph.export docs/design/graph.mmd docs/diagrams/langgraph.mmd
	npx -y @mermaid-js/mermaid-cli -q -c docs/diagrams/mermaid.config.json -b white \
		-i docs/diagrams/langgraph.mmd -o docs/diagrams/langgraph.svg

clean: ## Remove caches (never data or volumes)
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -r {} +
