# ADR-0012: Long-query mode for UPSC aspirants and advocates

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** Shubham Kumar Gupta

## Context
Two target groups ask long questions: UPSC aspirants (analytical, multi-Article, exam-format answers) and
advocates (long fact scenarios that map to several provisions). The original limits — 1,000-char input,
3 sub-queries, 8 context chunks, 700-token answers, 8B router — cut these short.

## Decision
- Raise the input limit to 4,000 chars.
- Above `LONG_QUERY_CHARS` (500), use the 70B model as router, allow up to 5 sub-queries, and apply **issue
  spotting** for fact scenarios (one sub-query per legal issue).
- The router returns `answer_style`: `brief` (default), `detailed` or `exam`. `detailed`/`exam` answers get up
  to 15 chunks / 8K context tokens and 1,500 output tokens.
- Route `detailed`/`exam` answers to Gemini Flash (higher tokens/minute on the free tier), with the Groq 70B model
  as fallback.
- All limits are config values, documented in HLD §13.2.
- Add a `long_query` category to the golden set.

## Alternatives considered
- Keep one answer size for everything — simpler, but long questions get shallow answers.
- Always use the large context and 70B router — wastes quota and latency on simple questions.
- Paid model for long answers — better headroom; revisit if free-tier limits bite.

## Consequences
- Long answers take ~7–12 s to complete (streaming starts in ~2 s).
- Two answer-model paths to evaluate; the golden set covers both.
- Advocates still lack case law (HLD L4, L11).

## Revisit when
Long-answer faithfulness is below threshold, free-tier tokens/minute limits cause frequent fallbacks, or case law
is added to the corpus.
