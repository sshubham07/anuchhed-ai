# ADR-0010: Use a different model family as the RAGAS judge

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
LLM judges tend to rate outputs from their own model family more favourably.

## Decision
Generator is Llama (Groq); judge is Gemini Flash at temperature 0, fallback gpt-oss-120b. Judge results are cached.

## Alternatives considered
- Same model as judge — cheaper setup, but biased scores.

## Consequences
Two providers needed for eval; scores are more trustworthy.

## Revisit when
A clearly better judge becomes available on free tiers.
