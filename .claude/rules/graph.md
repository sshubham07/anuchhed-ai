---
paths:
  - "src/samvidhan/graph/**"
---

# LangGraph orchestration rules

- Spec: HLD §8.1.1. Decision: ADR-0011 (and ADR-0003: stays deterministic in v1).
- Nodes are thin async functions that call `memory/`, `query/`, `retrieval/`, `generation/` and return a partial
  `ChatState` update. Keep business logic out of `graph/`.
- Never import LangChain chains, retrievers, vector stores or chat models. LLM calls go through `llm/client.py`.
- No LangGraph checkpointer — chat history lives in our Postgres tables.
- Branching only via conditional edges on `route.type` (and, in v2, `low_confidence`). No node may let the LLM pick
  tools in v1.
- Changing the graph shape: update the Mermaid export (`docs/design/graph.mmd`) and HLD §8.1.1 in the same PR.
- Tests: unit-test nodes directly; one integration test runs the compiled graph end to end with `FakeLLM`.
