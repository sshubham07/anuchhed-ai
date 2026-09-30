# ADR-0003: Deterministic router pipeline instead of an agent

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta
- **Related:** implemented with LangGraph — see [ADR-0011](0011-langgraph-orchestration.md)

## Context
Queries need different handling (lookup, comparison, vague, out of scope). An agent that picks tools in a loop is flexible but slow, costly and hard to test, especially on free-tier quotas.

## Decision
One small-LLM call returns a typed `RouteDecision` (JSON). Plain Python branches on `type`. The path per query is fixed and testable.

## Alternatives considered
- Tool-calling agent for every query — 3–10 LLM calls, unpredictable latency.
- No routing — every query gets the same treatment, so refusals and comparisons suffer.

## Consequences
Two LLM calls in the request path; router accuracy becomes a measured metric.

## Revisit when
Low-confidence cases cluster into multi-hop questions → add an agent fallback only for those.
