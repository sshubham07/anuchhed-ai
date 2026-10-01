"""`/v1/chat` event mapping: graph stream → meta / token / citations / done / error
(spec: api-sessions-memory §3.5). Runs the compiled graph over FakeLLM and a stub retriever."""

import json
from typing import Any

import pytest

from samvidhan.api.routes.chat import collect, graph_events, sse
from samvidhan.core.config import Settings
from samvidhan.core.errors import LLMUnavailableError, SamvidhanError
from samvidhan.generation import templates
from samvidhan.graph.builder import build_graph
from samvidhan.graph.state import ChatState
from samvidhan.llm.fake import FakeProvider
from tests.unit.graph_helpers import make, router_reply

INITIAL: ChatState = {"request_id": "r1", "session_id": None, "message": "arrest?"}


async def _events(settings: Settings, provider: FakeProvider) -> list[tuple[str, dict[str, Any]]]:
    deps, _, _ = make(settings, provider)
    return [event async for event in graph_events(build_graph(deps), dict(INITIAL))]  # type: ignore[arg-type]


async def test_answer_streams_meta_tokens_citations_done(settings: Settings) -> None:
    provider = FakeProvider(
        {
            "router": router_reply(standalone_query="grounds of arrest", article_refs=["22"]),
            "answer": "You must be told the grounds [Art. 22]. Also [Art. 999].",
        }
    )
    events = await _events(settings, provider)
    names = [name for name, _ in events]
    assert names[0] == "meta" and names[-2:] == ["citations", "done"]
    assert set(names[1:-2]) == {"token"}
    meta = events[0][1]
    assert meta["route_type"] == "simple" and meta["refs"] == ["22"]
    assert meta["standalone_query"] == "grounds of arrest" and meta["request_id"] == "r1"
    citations = events[-2][1]
    assert [c["ref"] for c in citations["citations"]] == ["22"]
    assert citations["invalid"] == ["999"]
    done = events[-1][1]
    streamed = "".join(data["text"] for name, data in events if name == "token")
    assert "[Art. 999]" in streamed and "[Art. 999]" not in done["answer"]
    assert done["answer"].endswith("Not legal advice._")
    assert {"route", "retrieve", "generate", "ttft", "save_turn"} <= set(done["latency_ms"])
    assert done["message_id"] is None  # in-memory store


async def test_template_route_keeps_the_event_order(settings: Settings) -> None:
    events = await _events(settings, FakeProvider({"router": router_reply(type="out_of_scope")}))
    assert [name for name, _ in events] == ["meta", "token", "citations", "done"]
    assert events[1][1]["text"] == templates.OUT_OF_SCOPE
    assert events[2][1] == {"citations": [], "invalid": []}


async def test_llm_failure_ends_with_one_error_event(settings: Settings) -> None:
    provider = FakeProvider(
        {"router": router_reply()},
        fail_models={settings.answer_model: "auth", settings.answer_fallback_model: "auth"},
    )
    events = await _events(settings, provider)
    assert [name for name, _ in events][0] == "meta"
    name, data = events[-1]
    assert name == "error"
    assert data["code"] == LLMUnavailableError.code and data["request_id"] == "r1"
    assert "done" not in [n for n, _ in events]


async def test_json_mode_shape(settings: Settings) -> None:
    deps, _, _ = make(
        settings,
        FakeProvider({"router": router_reply(), "answer": "Life is protected [Art. 21]."}),
    )
    body = await collect(graph_events(build_graph(deps), dict(INITIAL)))  # type: ignore[arg-type]
    assert body.answer.startswith("Life is protected [Art. 21].")
    assert [c.label for c in body.citations] == ["Art. 21"]
    assert body.route is not None and body.route.type == "simple"
    assert body.low_confidence is False and "total" not in body.latency_ms


async def test_json_mode_raises_the_original_error(settings: Settings) -> None:
    async def failing() -> Any:
        yield "error", {"code": "LLM_UNAVAILABLE", "message": "down", "request_id": "r1"}

    with pytest.raises(LLMUnavailableError):
        await collect(failing())
    with pytest.raises(SamvidhanError):  # unknown code → generic 500
        await collect(_one("error", {"code": "NOPE", "message": "x", "request_id": None}))


async def _one(name: str, data: dict[str, Any]) -> Any:
    yield name, data


def test_sse_format() -> None:
    assert sse("token", {"text": "Art. 21 – जीवन"}) == (
        'event: token\ndata: {"text": "Art. 21 – जीवन"}\n\n'
    )
    assert json.loads(sse("done", {"a": 1}).split("data: ")[1]) == {"a": 1}


# ---- UI support: answer_style override and the debug block (spec: api-sessions-memory §10) ----


async def _run(
    settings: Settings, provider: FakeProvider, *, debug: bool = False, **initial: Any
) -> list[tuple[str, dict[str, Any]]]:
    deps, _, _ = make(settings, provider)
    state = {**INITIAL, **initial}
    return [e async for e in graph_events(build_graph(deps), state, debug=debug)]  # type: ignore[arg-type]


@pytest.mark.parametrize(("override", "expected"), [("exam", "exam"), (None, "detailed")])
async def test_answer_style_override_replaces_the_router_choice(
    settings: Settings, override: str | None, expected: str
) -> None:
    provider = FakeProvider(
        {"router": router_reply(answer_style="detailed"), "answer": "Life [Art. 21]."}
    )
    events = await _run(settings, provider, answer_style_override=override)
    assert events[0] == ("meta", events[0][1]) and events[0][1]["answer_style"] == expected


async def test_answer_style_override_leaves_template_routes_alone(settings: Settings) -> None:
    provider = FakeProvider({"router": router_reply(type="chitchat")})
    events = await _run(settings, provider, answer_style_override="exam")
    assert events[0][1]["answer_style"] == "brief"


async def test_debug_block_only_when_enabled(settings: Settings) -> None:
    provider = FakeProvider({"router": router_reply(), "answer": "Life is protected [Art. 21]."})
    assert "debug" not in (await _run(settings, provider))[-1][1]
    done = (await _run(settings, provider, debug=True))[-1][1]
    debug = done["debug"]
    assert debug["route"]["type"] == "simple"
    assert debug["chunks"] and debug["chunks"][0]["ref"] == "21"
    assert debug["chunks"][0]["label"] == "Art. 21"
    assert set(debug) == {"route", "chunks", "citation_stats", "invalid_citations"}
    json.dumps(done)  # SSE-serializable


async def test_debug_block_for_template_route_has_no_chunks(settings: Settings) -> None:
    provider = FakeProvider({"router": router_reply(type="out_of_scope")})
    debug = (await _run(settings, provider, debug=True))[-1][1]["debug"]
    assert debug["chunks"] == [] and debug["route"]["type"] == "out_of_scope"
