# Samvidhan RAG

Conversational Q&A over the **Constitution of India**. Ask in plain English, get answers grounded in the official
text with Article-level citations, including follow-up questions.

> **Status:** design phase — specs drafted and awaiting approval; no application code yet.
> Informational only, not legal advice.

## How it works

```
PDF → structure-aware chunks (one per Article) → bge-m3 embeddings in Postgres/pgvector
Question → LangGraph pipeline: router LLM (classify + rewrite follow-ups) → hybrid search (vector + full-text, RRF)
         → reranker → answer LLM with citations → streamed to the UI
```

Full design: [docs/design/HLD.md](docs/design/HLD.md) · Decisions: [docs/adr/](docs/adr/README.md)

## Tech stack

Python 3.12 · FastAPI · LangGraph · PostgreSQL 16 + pgvector · BAAI/bge-m3 · bge-reranker-v2-m3 · LiteLLM (Groq, Gemini) ·
Streamlit · RAGAS · Docker Compose

## Quick start (once Phase 0 lands)

```bash
cp .env.example .env        # add GROQ_API_KEY and GEMINI_API_KEY
make setup                  # uv sync + pre-commit install
make up && make migrate     # Postgres + pgvector on localhost:5433, run migrations
make ingest                 # parse and index the Constitution PDF (data/raw/)
make run                    # API on :8000
make ui                     # Streamlit on :8501
```

## Quality

Measured on a golden set of 150+ questions and 20 multi-turn conversations. Gates include Recall@5 ≥ 0.90 and
RAGAS faithfulness ≥ 0.85. See [docs/specs/evaluation.md](docs/specs/evaluation.md). Results table will be added at
v1.0.

## Limitations

Text only (no images/scanned PDFs), English answers, one edition of the Constitution, no case law or other
statutes. Full list and all system limits (input size, answer length, rate limits, quotas): HLD §13.

## Documentation

| Doc | Purpose |
|-----|---------|
| [docs/design/HLD.md](docs/design/HLD.md) | High-level design |
| [docs/adr/](docs/adr/README.md) | Architecture decision records |
| [docs/specs/](docs/specs/) | Component specs (evaluation, observability) + template |
| [docs/plans/implementation-plan.md](docs/plans/implementation-plan.md) | Phased delivery plan |
| [docs/standards/engineering-standards.md](docs/standards/engineering-standards.md) | Coding standards |
| [docs/runbooks/](docs/runbooks/README.md) | Operational runbooks |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [AGENTS.md](AGENTS.md) | Instructions for AI coding agents |
