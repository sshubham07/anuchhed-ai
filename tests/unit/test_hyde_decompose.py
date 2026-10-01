"""HyDE passage and multi-part merge (spec §3.6)."""

import structlog.testing

from samvidhan.core.config import Settings
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.prompts import load_prompt
from samvidhan.query.decompose import merge_results
from samvidhan.query.hyde import hyde_passage
from samvidhan.retrieval.types import RetrievalResult, ScoredChunk
from tests.unit.retrieval_helpers import chunk


def result(
    *chunks: ScoredChunk, top: float | None = 0.9, low: bool = False, refs: list[str] | None = None
) -> RetrievalResult:
    return RetrievalResult(
        chunks=list(chunks),
        candidates=list(chunks),
        ranked=list(chunks),
        refs=refs or [],
        top_score=top,
        low_confidence=low,
        latency_ms={},
        trace={"mode": "hybrid_rerank"},
    )


async def test_hyde_returns_the_passage(settings: Settings) -> None:
    prompt = load_prompt(settings.prompts_dir, settings.hyde_prompt_version)
    provider = FakeProvider({"hyde": "  The State shall protect the environment.  "})
    passage = await hyde_passage(fake_llm(provider), settings, prompt, "environment?")
    assert passage == "The State shall protect the environment."
    call = provider.calls[0]
    assert call.purpose == "hyde" and call.max_tokens == settings.hyde_max_tokens
    assert "<question>\nenvironment?\n</question>" in call.messages[-1]["content"]


async def test_hyde_failure_returns_none(settings: Settings) -> None:
    prompt = load_prompt(settings.prompts_dir, settings.hyde_prompt_version)
    s = settings.model_copy(update={"router_fallback_model": ""})
    provider = FakeProvider({"hyde": "x"}, fail_models={s.router_model: "auth"})
    with structlog.testing.capture_logs() as logs:
        assert await hyde_passage(fake_llm(provider), s, prompt, "q") is None
    assert any(e["event"] == "hyde_failed" for e in logs)


A1, A2, A3 = (chunk(f"art-{n}#0", article_no=str(n)) for n in (1, 2, 3))
B1, B2 = (chunk(f"art-{n}#0", article_no=str(n)) for n in (11, 12))
PIN = chunk("art-32#0", article_no="32", pinned=True)


def test_merge_round_robins_pinned_first_and_dedupes() -> None:
    merged = merge_results(
        ["a", "b"],
        [result(PIN, A1, A2, A3, top=0.4), result(PIN, B1, A1, B2, top=0.7)],
        per_query_k=2,
        cap=8,
    )
    assert [c.id for c in merged.chunks] == ["art-32#0", "art-1#0", "art-11#0", "art-2#0"]
    assert merged.top_score == 0.7 and not merged.low_confidence
    assert [s["query"] for s in merged.trace["sub_queries"]] == ["a", "b"]


def test_merge_caps_and_logs() -> None:
    with structlog.testing.capture_logs() as logs:
        merged = merge_results(
            ["a", "b"], [result(A1, A2, A3), result(B1, B2)], per_query_k=3, cap=3
        )
    assert [c.id for c in merged.chunks] == ["art-1#0", "art-11#0", "art-2#0"]
    assert any(e["event"] == "limit_applied" and e["limit"] == "context_chunks" for e in logs)


def test_merge_low_confidence_only_when_every_part_is_weak() -> None:
    weak, strong = result(A1, top=0.01, low=True), result(B1, top=0.9)
    assert not merge_results(["a", "b"], [weak, strong], per_query_k=3, cap=8).low_confidence
    assert merge_results(["a", "b"], [weak, weak], per_query_k=3, cap=8).low_confidence
    assert merge_results(["a"], [result(top=None)], per_query_k=3, cap=8).low_confidence
