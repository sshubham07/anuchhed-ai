"""Router: parsing, fallback, model choice and post-processing (spec §3.5)."""

import json
from typing import Any

import pytest
import structlog.testing

from samvidhan.core.config import Settings
from samvidhan.core.errors import LLMBusyError
from samvidhan.llm.budget import BudgetGuard
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.prompts import PromptTemplate, load_prompt
from samvidhan.llm.recorder import MemoryCallRecorder
from samvidhan.memory.types import ChatMessage, SessionMemory
from samvidhan.query.router import RouteDecision, postprocess, route


@pytest.fixture
def prompt(settings: Settings) -> PromptTemplate:
    return load_prompt(settings.prompts_dir, settings.router_prompt_version)


def decision_json(**fields: Any) -> str:
    return json.dumps({"type": "simple", "standalone_query": "q", **fields})


async def _route(
    settings: Settings,
    prompt: PromptTemplate,
    reply: str,
    message: str = "What does Art. 21-A say?",
    memory: SessionMemory | None = None,
    **provider_kwargs: Any,
) -> tuple[RouteDecision, FakeProvider, MemoryCallRecorder]:
    provider = FakeProvider({"router": reply}, **provider_kwargs)
    recorder = MemoryCallRecorder()
    decision = await route(
        fake_llm(provider, recorder), settings, prompt, message, memory or SessionMemory()
    )
    return decision, provider, recorder


async def test_valid_json_becomes_a_decision(settings: Settings, prompt: PromptTemplate) -> None:
    reply = decision_json(
        type="article_lookup",
        standalone_query="What does Article 21A say?",
        article_refs=["21A"],
        answer_style="brief",
        reason="names an Article",
    )
    decision, provider, recorder = await _route(settings, prompt, reply)
    assert decision.type == "article_lookup" and decision.refs == ["21A"]
    assert not decision.fallback
    call = provider.calls[0]
    assert call.json_mode and call.temperature == settings.router_temperature
    assert call.max_tokens == settings.router_max_tokens and call.model == settings.router_model
    assert recorder.records[0].purpose == "router"
    assert recorder.records[0].prompt_version == "router.v1"


@pytest.mark.parametrize(
    ("reply", "kwargs", "reason"),
    [
        ("not json at all", {}, "invalid_json"),
        ('{"type": "teleport", "standalone_query": "q"}', {}, "invalid_json"),
        (decision_json(), {"finish_reason": "length"}, "truncated"),
        (
            decision_json(),
            {"fail_models": {"groq/llama-3.1-8b-instant": "auth"}},
            "llm_unavailable",
        ),
    ],
)
async def test_unusable_output_falls_back_to_simple(
    settings: Settings, prompt: PromptTemplate, reply: str, kwargs: dict[str, Any], reason: str
) -> None:
    s = settings.model_copy(update={"router_fallback_model": ""})
    with structlog.testing.capture_logs() as logs:
        decision, _, _ = await _route(s, prompt, reply, message="raw question", **kwargs)
    assert decision.type == "simple" and decision.fallback
    assert decision.standalone_query == "raw question"
    warning = next(e for e in logs if e["event"] == "router_fallback")
    assert warning["reason"] == reason


async def test_busy_is_not_swallowed(settings: Settings, prompt: PromptTemplate) -> None:
    async def at_cap(model: str) -> int:
        return 10**6

    s = settings.model_copy(update={"router_fallback_model": ""})
    budget = BudgetGuard(at_cap, {s.router_model: 10}, warn_ratio=0.8)
    llm = fake_llm(FakeProvider({"router": decision_json()}), budget=budget)
    with pytest.raises(LLMBusyError):
        await route(llm, s, prompt, "q", SessionMemory())


async def test_json_in_code_fences_is_accepted(settings: Settings, prompt: PromptTemplate) -> None:
    reply = "```json\n" + decision_json(type="chitchat") + "\n```"
    decision, _, _ = await _route(settings, prompt, reply, message="hi")
    assert decision.type == "chitchat" and not decision.fallback


async def test_long_message_uses_long_router(settings: Settings, prompt: PromptTemplate) -> None:
    message = "x" * (settings.long_query_chars + 1)
    _, provider, _ = await _route(settings, prompt, decision_json(), message=message)
    assert provider.calls[0].model == settings.router_model_long
    assert provider.calls[0].timeout_s == settings.llm_timeout_long_s
    message = "x" * settings.long_query_chars
    _, provider, _ = await _route(settings, prompt, decision_json(), message=message)
    assert provider.calls[0].model == settings.router_model


async def test_prompt_carries_memory_and_recent_history(
    settings: Settings, prompt: PromptTemplate
) -> None:
    memory = SessionMemory(
        last_articles=["21"],
        articles_discussed=["14", "21"],
        messages=[ChatMessage("user", f"old {n}") for n in range(10)],
    )
    _, provider, _ = await _route(
        settings, prompt, decision_json(), message="what are its exceptions?", memory=memory
    )
    user = provider.calls[0].messages[-1]["content"]
    assert "last_articles: 21" in user and "articles_discussed: 14, 21" in user
    assert "old 9" in user and "old 3" not in user  # last ROUTER_HISTORY_MESSAGES only
    assert "<message>\nwhat are its exceptions?\n</message>" in user


def test_sub_queries_capped_and_cleared_for_other_types(settings: Settings) -> None:
    many = [f"q{n}" for n in range(8)]
    with structlog.testing.capture_logs() as logs:
        multi = postprocess(
            RouteDecision(type="multi_part", standalone_query="q", sub_queries=many),
            "short",
            settings,
        )
    assert multi.sub_queries == many[: settings.max_sub_queries]
    limit = next(e for e in logs if e["event"] == "limit_applied")
    assert limit == {**limit, "limit": "sub_queries", "requested": 8, "allowed": 3}

    long_message = "x" * (settings.long_query_chars + 1)
    long = postprocess(
        RouteDecision(type="multi_part", standalone_query="q", sub_queries=many),
        long_message,
        settings,
    )
    assert len(long.sub_queries) == settings.max_sub_queries_long

    simple = postprocess(
        RouteDecision(type="simple", standalone_query="q", sub_queries=["a", "b"]), "m", settings
    )
    assert simple.sub_queries == []


def test_lookup_without_refs_and_empty_standalone(settings: Settings) -> None:
    decision = postprocess(
        RouteDecision(type="article_lookup", standalone_query="  "), "the message", settings
    )
    assert decision.type == "simple" and decision.standalone_query == "the message"
