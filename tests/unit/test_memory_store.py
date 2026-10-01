"""Structured memory update and the in-process store (HLD §9.3)."""

import dataclasses
import uuid

from samvidhan.generation import templates
from samvidhan.memory.store import InMemorySessionStore
from samvidhan.memory.structured import (
    ARTICLES_DISCUSSED_MAX,
    RECENT_TOPICS_MAX,
    apply_turn,
    from_json,
    to_json,
)
from samvidhan.memory.types import ChatMessage, SessionMemory, Turn


def turn(refs: list[str], topic: str = "topic", route_type: str = "simple") -> Turn:
    return Turn(
        user="q", assistant="a", standalone_query=topic, cited_refs=refs, route_type=route_type
    )


def test_apply_turn_rolls_memory_forward() -> None:
    memory = apply_turn(SessionMemory(), turn(["21", "22"], "arrest"), max_messages=4)
    memory = apply_turn(memory, turn(["14"], "equality"), max_messages=4)
    assert memory.last_articles == ["14"]
    assert memory.articles_discussed == ["21", "22", "14"]
    assert memory.recent_topics == ["arrest", "equality"]
    assert [m.role for m in memory.messages] == ["user", "assistant", "user", "assistant"]


def test_caps_and_uncited_turns() -> None:
    memory = SessionMemory()
    for n in range(20):
        memory = apply_turn(memory, turn([str(n)], f"t{n}"), max_messages=6)
    assert len(memory.articles_discussed) == ARTICLES_DISCUSSED_MAX
    assert memory.articles_discussed[-1] == "19"
    assert memory.recent_topics == [f"t{n}" for n in range(15, 20)][-RECENT_TOPICS_MAX:]
    assert len(memory.messages) == 6
    after_chitchat = apply_turn(memory, turn([], "hi", "chitchat"), max_messages=6)
    assert after_chitchat.last_articles == ["19"]  # kept, so the next follow-up still resolves
    assert after_chitchat.recent_topics == memory.recent_topics


def test_parts_discussed_roll_forward_and_json_round_trip() -> None:
    memory = apply_turn(
        SessionMemory(), dataclasses.replace(turn(["21"]), cited_parts=["III"]), max_messages=4
    )
    memory = apply_turn(
        memory, dataclasses.replace(turn(["48A", "21"]), cited_parts=["IV", "III"]), max_messages=4
    )
    assert memory.parts_discussed == ["IV", "III"]
    data = to_json(memory)
    assert set(data) == {"last_articles", "articles_discussed", "parts_discussed", "recent_topics"}
    history = [ChatMessage("user", "q")]
    restored = from_json(data, summary="s", messages=history)
    assert restored == dataclasses.replace(memory, summary="s", messages=history)
    assert from_json({"last_articles": "21", "bogus": 1}, summary=None, messages=[]) == (
        SessionMemory()
    )  # malformed jsonb degrades to empty memory


async def test_store_keeps_sessions_apart_and_skips_none() -> None:
    store = InMemorySessionStore(max_messages=12)
    a, b = uuid.uuid4(), uuid.uuid4()
    saved = await store.save_turn(a, turn(["21"]))
    assert saved.message_id is None
    assert (await store.load(a)).last_articles == ["21"]
    assert (await store.load(b)).last_articles == []
    await store.save_turn(None, turn(["14"]))
    assert (await store.load(None)) == SessionMemory()


def test_templates() -> None:
    assert templates.clarification(None) == templates.DEFAULT_CLARIFICATION
    assert templates.clarification(" Which right? ") == "Which right?"
    assert templates.chitchat("thanks a lot").startswith("You're welcome")
    assert templates.chitchat("hello").startswith("Hello")
    assert "IPC/BNS" in templates.OUT_OF_SCOPE
