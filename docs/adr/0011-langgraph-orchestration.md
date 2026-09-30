# ADR-0011: LangGraph for pipeline orchestration only

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta
- **Related:** refines [ADR-0003](0003-deterministic-router-not-agent.md) (pipeline stays deterministic)

## Context
ADR-0003 chose a deterministic router pipeline over a free-roaming agent. The pipeline has clear stages
(load memory → route → branch → retrieve → rerank → generate → validate → save) with conditional branching on the
router's decision. Writing this as nested `if/elif` works, but the flow is harder to visualize, extend (the planned
low-confidence agent fallback is a loop) and stream step-by-step. LangGraph is also widely used in industry for
LLM workflows.

## Decision
Use **LangGraph** (`StateGraph`) to orchestrate the online pipeline:
- A typed `ChatState` carries the request through the graph.
- Each stage is a node (a plain async function returning a partial state update).
- Branching on `route.type` uses conditional edges.
- The API runs the graph with streaming and maps graph events to our SSE events.

Boundaries:
- **Not used:** LangChain chains, retrievers, vector stores, prompt templates or chat models. Retrieval stays in
  `retrieval/`, LLM calls stay in `llm/client.py` (LiteLLM + `llm_calls` logging).
- **Not used:** LangGraph checkpointer for chat memory. Sessions and history stay in our own Postgres tables
  (HLD §9), which keeps the data model, summary logic and retention under our control.
- The graph stays deterministic in v1: no node lets the LLM choose tools.

## Alternatives considered
- Plain Python `if/elif` orchestration — simplest, no dependency, but less visual and harder to extend with loops.
- LangChain LCEL chains — pulls in LangChain abstractions we don't want for retrieval/LLM calls.
- LlamaIndex query pipelines — similar trade-off; heavier framework.

## Consequences
- One extra dependency (`langgraph`, pinned). About half a day of learning.
- The graph can be exported as a Mermaid diagram for the README and design docs.
- Nodes are plain functions, so unit tests call them directly; one integration test runs the compiled graph with
  `FakeLLM`.
- The v2 low-confidence agent fallback becomes one extra node/subgraph with a bounded loop.

## Revisit when
LangGraph adds noticeable latency (> 20 ms per request overhead) or its API churn costs more than it saves.
