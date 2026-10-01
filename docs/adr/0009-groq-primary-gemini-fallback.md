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

## Update 2026-10-01
Groq retired `llama-3.1-8b-instant` and `llama-3.3-70b-versatile`. The decision stands (Groq primary, Gemini
fallback, via LiteLLM); only the model ids changed: router `groq/qwen/qwen3.8-27b`, answers
`groq/openai/gpt-oss-120b`, fallbacks `gemini/gemini-3.5-flash-lite` / `gemini/gemini-3.5-flash` (HLD §6). These
are reasoning models, so `MODEL_REASONING_EFFORT` keeps their hidden thinking low. Groq's free tier is now 1,000
requests/day and 8,000 tokens/minute per model.
