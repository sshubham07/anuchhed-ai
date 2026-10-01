"""Answer generation: prompt building, caps, model choice, truncation (spec §3.7)."""

import pytest
import structlog.testing

from samvidhan.core.config import Settings
from samvidhan.generation.answer import (
    answer_request,
    build_messages,
    fit_context,
    generate_answer,
    render_excerpts,
)
from samvidhan.generation.citations import TRUNCATION_NOTE
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.prompts import PromptTemplate, load_prompt
from samvidhan.memory.types import ChatMessage, SessionMemory
from samvidhan.query.router import RouteDecision
from samvidhan.retrieval.types import ScoredChunk
from tests.unit.retrieval_helpers import chunk

ROUTE = RouteDecision(type="simple", standalone_query="What does Article 21 protect?")


@pytest.fixture
def prompt(settings: Settings) -> PromptTemplate:
    return load_prompt(settings.prompts_dir, settings.answer_prompt_version)


def titled(chunk_id: str, article_no: str, title: str, text: str) -> ScoredChunk:
    from dataclasses import replace

    return replace(chunk(chunk_id, article_no=article_no, text=text), article_title=title)


ART21 = titled("art-21#0", "21", 'Protection of "life"', "No person shall be deprived of life.")


def test_excerpts_carry_id_cite_and_escaped_title() -> None:
    rendered = render_excerpts([ART21, chunk("sch-7-list2#0", schedule_no="7", text="Police.")])
    assert '<excerpt id="art-21#0" cite="Art. 21" title="Protection of &quot;life&quot;">' in (
        rendered
    )
    assert '<excerpt id="sch-7-list2#0" cite="Sch. 7" title="">\nPolice.\n</excerpt>' in rendered


def test_fit_context_keeps_order_within_token_budget() -> None:
    chunks = [chunk(f"c{n}", article_no=str(n), text="x" * 400) for n in range(5)]  # ~101 each
    with structlog.testing.capture_logs() as logs:
        kept = fit_context(chunks, max_tokens=250)
    assert [c.id for c in kept] == ["c0", "c1"]
    assert logs[0]["event"] == "limit_applied" and logs[0]["limit"] == "context_tokens"
    assert [c.id for c in fit_context(chunks[:1], max_tokens=1)] == ["c0"]  # first always fits


def test_messages_include_history_note_and_question(
    settings: Settings, prompt: PromptTemplate
) -> None:
    memory = SessionMemory(
        articles_discussed=["14"], messages=[ChatMessage("user", f"m{n}") for n in range(5)]
    )
    _, user = build_messages(
        prompt,
        settings,
        message="what about it?",
        route=ROUTE,
        context=[ART21],
        memory=memory,
        low_confidence=True,
    )
    content = user["content"]
    assert "user: m4" in content and "user: m2" not in content  # ANSWER_HISTORY_MESSAGES = 2
    assert "<articles_discussed>14</articles_discussed>" in content
    assert "<retrieval_note>" in content and "<question>\nwhat about it?\n</question>" in content
    assert "<standalone_question>What does Article 21 protect?</standalone_question>" in content


@pytest.mark.parametrize(
    ("style", "model_key", "tokens_key"),
    [
        ("brief", "answer_model", "answer_max_tokens"),
        ("detailed", "answer_model_long", "answer_max_tokens_long"),
        ("exam", "answer_model_long", "answer_max_tokens_long"),
    ],
)
def test_answer_style_picks_model_and_limits(
    settings: Settings, prompt: PromptTemplate, style: str, model_key: str, tokens_key: str
) -> None:
    route = ROUTE.model_copy(update={"answer_style": style})
    request = answer_request(settings, prompt, route, [])
    assert request.models[0] == getattr(settings, model_key)
    assert request.max_tokens == getattr(settings, tokens_key)
    assert request.purpose == "answer" and request.prompt_version == "answer.v1"


async def test_generate_streams_tokens(settings: Settings, prompt: PromptTemplate) -> None:
    provider = FakeProvider({"answer": "No person shall be deprived of life [Art. 21]."})
    seen: list[str] = []
    draft = await generate_answer(
        fake_llm(provider),
        settings,
        prompt,
        message="What does Article 21 say?",
        route=ROUTE,
        chunks=[ART21],
        memory=SessionMemory(),
        low_confidence=False,
        on_token=seen.append,
    )
    assert draft.text == "No person shall be deprived of life [Art. 21]."
    assert "".join(seen) == draft.text and draft.context == [ART21]
    assert "<retrieval_note>" not in provider.calls[0].messages[-1]["content"]


async def test_truncated_answer_gets_the_note(settings: Settings, prompt: PromptTemplate) -> None:
    provider = FakeProvider({"answer": "A long answer"}, finish_reason="length")
    with structlog.testing.capture_logs() as logs:
        draft = await generate_answer(
            fake_llm(provider),
            settings,
            prompt,
            message="q",
            route=ROUTE,
            chunks=[ART21],
            memory=SessionMemory(),
            low_confidence=False,
        )
    assert draft.text.endswith(f"_{TRUNCATION_NOTE}_")
    event = next(e for e in logs if e["event"] == "answer_truncated")
    assert event["max_tokens"] == settings.answer_max_tokens
