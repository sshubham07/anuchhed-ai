# AGENTS.md — Samvidhan RAG (Constitution of India Q&A)

Instructions for AI coding agents working in this repo (Claude Code, Cursor, Codex, Copilot, etc.).
Humans: start with `README.md` and `CONTRIBUTING.md`.

A conversational RAG service that answers questions about the **Constitution of India** using only the official
text, with Article-level citations. Text in, text out.

> **Read before any work:** `docs/design/HLD.md` (architecture, limits) and `docs/adr/` (decisions).
> Component detail: `docs/specs/foundation.md`, `docs/specs/evaluation.md`, `docs/specs/observability.md`.
> Execution order: `docs/plans/implementation-plan.md`.
> Coding rules: `docs/standards/engineering-standards.md`.

## Golden rules (non-negotiable)

1. **Spec → Plan → Code.** No feature code without an approved spec in `docs/specs/` and a phase/task in
   `docs/plans/`. If a change isn't covered, write or update the spec first
   (template: `docs/specs/_template.md`). Architectural changes also need a new ADR in `docs/adr/`.
2. **Answers come only from retrieved chunks.** Never add prompt text that lets the model use outside knowledge.
   Every answer cites Articles; if nothing relevant is retrieved, the bot says so.
3. **Retrieval is stateless.** Chat history is used only by the router/condense step and the answer prompt — never
   passed into search.
4. **Eval gates merges.** Any change to chunking, embeddings, retrieval, prompts or models must run the eval suite
   (`make eval-retrieval`, `make eval-full`) and must not drop any metric below the thresholds in `docs/specs/evaluation.md`.
5. **Models are config, not code.** Model IDs live in `.env` / `config.py`. Never hard-code a model name.
6. **Log every LLM call** to the `llm_calls` table and structured logs (see observability spec).
7. **Keep it simple.** LangGraph orchestrates the pipeline only (ADR-0011). No LangChain chains/retrievers/
   vector stores/chat models, no LlamaIndex, no LangGraph checkpointer, no extra infra. One Postgres.

## Tech stack (summary)

Python 3.12 · uv · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) + asyncpg · Alembic · PostgreSQL 16 + pgvector ·
LangGraph (orchestration) · BAAI/bge-m3 (embeddings) · BAAI/bge-reranker-v2-m3 · LiteLLM (Groq primary, Gemini
fallback) · static HTML/CSS/JS UI served by FastAPI (ADR-0013) ·
structlog · pytest · RAGAS · Docker Compose.

## Repository layout

```
src/samvidhan/
  api/            # FastAPI routers, request/response schemas, deps
  core/           # config, logging, errors, ids
  ingestion/      # pdf parsing, article segmentation, chunking, indexing CLI
  retrieval/      # dense, lexical, fusion (RRF), rerank, article lookup
  graph/          # LangGraph: ChatState, nodes (thin wrappers), graph builder, Mermaid export
  query/          # router/condense (structured output), HyDE, decomposition
  generation/     # answer prompt, citation extraction, streaming
  memory/         # sessions, history window, structured memory, summarizer
  llm/            # LiteLLM wrapper, fallbacks, llm_calls logging, cost
  db/             # models, repositories, migrations (alembic)
ui/               # static web UI (HTML/CSS/JS, served at / by the API; talks to API only)
eval/             # golden sets, runners, RAGAS, reports
tests/            # unit/ integration/ e2e/
prompts/          # versioned prompt templates (*.md / *.j2)
data/             # raw PDF (gitignored), processed chunks JSONL
```

## Common commands

```bash
uv sync                                   # install
docker compose up -d --wait db            # Postgres + pgvector on host port 5433 (or: make up)
uv run alembic upgrade head               # migrations
uv run python -m samvidhan.ingestion.cli ingest data/raw/constitution.pdf
uv run uvicorn --factory samvidhan.api.main:create_app --reload   # or: make run
open http://localhost:8000/                # web UI (served by the API)
uv run pytest -m "not slow"               # fast tests
uv run pytest                             # all tests
uv run python -m eval.run --suite retrieval   # retrieval metrics (no LLM cost)
uv run python -m eval.run --suite full        # router + retrieval + RAGAS
uv run ruff check . && uv run ruff format . && uv run mypy src
```

## Workflow for agents

- Before coding: read the relevant spec section and the current phase in the plan. Restate the task's acceptance
  criteria in your first message.
- Work in small, reviewable steps. Write/adjust tests with the code.
- After coding: run ruff, mypy and pytest. For RAG-affecting changes, run the retrieval eval and paste the metric diff.
- Follow `CONTRIBUTING.md` for branches, commits and PRs (fill the PR template checklist).
- Update the plan checkbox and, if behaviour changed, the spec (specs are living documents).
- Never commit `.env`, the raw PDF, model weights or eval reports with API keys.
- If a requirement is ambiguous, ask — don't invent behaviour.

## Out of scope (v1)

Images, scanned PDFs/OCR, file uploads, voice, non-English answers, case law, IPC/BNS or any other statute,
legal advice, multi-document corpora, user accounts (anonymous sessions only). See HLD §13.
