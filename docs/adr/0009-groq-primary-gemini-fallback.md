# ADR-0009: Groq primary, Gemini fallback, via LiteLLM

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
Free tiers have daily caps and occasional outages; model lineups change.

## Decision
Call models through LiteLLM with provider fallback. Small model for router/HyDE/summary, 70B model for answers. Model ids live in config. A daily budget guard reads usage from `llm_calls`.

## Alternatives considered
- Single provider — outage or quota = downtime.
- Local Ollama for answers — weaker refusal and citation behaviour at small sizes.

## Consequences
Two sets of API keys; answers may differ slightly between providers (eval covers both).

## Revisit when
Quotas change, quality gaps appear, or a paid tier is justified.
