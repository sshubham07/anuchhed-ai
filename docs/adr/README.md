# Architecture Decision Records

We use lightweight ADRs (MADR-style). One decision per file; never edit an accepted ADR's decision — supersede it with a new ADR.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-structure-aware-chunking.md) | Structure-aware chunking (one Article per chunk) | Accepted |
| [0002](0002-hybrid-retrieval-with-rerank.md) | Hybrid retrieval (dense + Postgres FTS, RRF) with a cross-encoder reranker | Accepted |
| [0003](0003-deterministic-router-not-agent.md) | Deterministic router pipeline instead of an agent | Accepted |
| [0004](0004-router-extracts-article-refs.md) | Router LLM extracts Article references (no separate regex step) | Accepted |
| [0005](0005-postgres-single-store.md) | PostgreSQL + pgvector as the single data store | Accepted |
| [0006](0006-anonymous-sessions.md) | Anonymous sessions; authentication later | Accepted |
| [0007](0007-structured-memory-then-llm-summary.md) | Structured memory in v1; LLM rolling summary in v1.1 behind a flag | Accepted |
| [0008](0008-local-embeddings-and-reranker.md) | Local bge-m3 embeddings and bge-reranker-v2-m3 | Accepted |
| [0009](0009-groq-primary-gemini-fallback.md) | Groq primary, Gemini fallback, via LiteLLM | Accepted |
| [0010](0010-different-family-eval-judge.md) | Use a different model family as the RAGAS judge | Accepted |
| [0011](0011-langgraph-orchestration.md) | LangGraph for pipeline orchestration only | Accepted |
| [0012](0012-long-query-mode.md) | Long-query mode (answer styles, issue spotting, higher limits) | Accepted |
| [0013](0013-static-web-ui.md) | Static web UI served by the API, instead of Streamlit | Accepted |
