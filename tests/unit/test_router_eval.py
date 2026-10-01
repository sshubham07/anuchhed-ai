"""Router eval metrics and golden validation (spec §3.11, evaluation.md §3.2)."""

import json
from pathlib import Path

import pytest

from eval.golden import GoldenCase, GoldenConversation, load_cases
from eval.metrics import set_f1
from eval.router_suite import (
    ROUTER_GATES,
    RouterCaseResult,
    TurnResult,
    aggregate,
    aggregate_turns,
    contains_token,
    gates_for,
    gold_memory,
    markdown,
)
from samvidhan.query.router import RouteDecision


@pytest.mark.parametrize(
    ("got", "want", "f1"),
    [
        ([], [], 1.0),
        (["21"], ["21"], 1.0),
        (["21"], [], 0.0),
        ([], ["21"], 0.0),
        (["32", "226"], ["32"], 2 / 3),
    ],
)
def test_set_f1(got: list[str], want: list[str], f1: float) -> None:
    assert set_f1(got, want) == pytest.approx(f1)


def case(**fields: object) -> GoldenCase:
    base = {"id": "X", "split": "dev", "category": "simple", "question": "q",
            "expected_type": "simple"}  # fmt: skip
    return GoldenCase.model_validate(base | fields)


def test_expected_type_must_be_a_router_type() -> None:
    with pytest.raises(ValueError, match="not a router type"):
        case(expected_type="schedule")


def test_repo_golden_set_loads() -> None:
    assert load_cases()  # every case validates, incl. expected_type


def test_aggregate_and_markdown() -> None:
    results = [
        RouterCaseResult(case(id="A", question_refs=["21"], expected_type="article_lookup"),
                         RouteDecision(type="article_lookup", standalone_query="q",
                                       article_refs=["21"]), ["21"], 300),
        RouterCaseResult(case(id="B"), RouteDecision(type="conceptual", standalone_query="q"),
                         [], 400),
        RouterCaseResult(case(id="C", expected_answer_style="exam"),
                         RouteDecision(type="simple", standalone_query="q", fallback=True), [], 10),
    ]  # fmt: skip
    metrics = aggregate(results)
    assert metrics["type_accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["refs_f1"] == 1.0 and metrics["refs_f1_with_refs"] == 1.0
    assert metrics["json_validity"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["answer_style_accuracy"] == pytest.approx(2 / 3, abs=1e-4)

    report = {
        "run_id": "r", "git_sha": "abc", "golden_version": "0.2", "split": "dev",
        "config": {"router_model": "m", "router_model_long": "M", "router_prompt": "router.v1"},
        "metrics": metrics, "latency": {"p50_ms": 300, "p95_ms": 400},
        "gates": [{"metric": "type_accuracy", "value": 0.67, "op": ">=", "threshold": 0.9,
                   "baseline": None, "status": "FAIL"}],
        "confusion": [{"expected": "simple", "got": "conceptual", "n": 1}],
        "by_category": {"simple": metrics},
        "failures": [{"id": "B", "category": "simple", "expected_type": "simple",
                      "got_type": "conceptual", "expected_refs": [], "got_refs": [],
                      "expected_style": "brief", "got_style": "brief", "fallback": False,
                      "question": "q", "reason": "r"}],
    }  # fmt: skip
    text = markdown(report)
    assert "| type_accuracy | 0.670 | >= 0.90 | — | FAIL |" in text
    assert "**B** (simple) type simple→conceptual" in text
    json.dumps(report)  # report is JSON-serialisable


def test_golden_version_bumped() -> None:
    assert (Path("eval/golden/VERSION").read_text().strip()) == "0.3"


@pytest.mark.parametrize(
    ("text", "token", "found"),
    [
        ("exceptions to Article 21 personal liberty", "21", True),
        ("What does Article 210 say?", "21", False),
        ("Right to education under Article 21A", "21", False),
        ("Who conducts elections to the Panchayats?", "Panchayat", True),
        ("Can the money bill be amended?", "Money Bill", True),
        ("Who is the summoner?", "Parliament", False),
    ],
)
def test_contains_token(text: str, token: str, found: bool) -> None:
    assert contains_token(text, token) is found


def conversation(*turns: dict[str, object]) -> GoldenConversation:
    return GoldenConversation.model_validate({"id": "MT-X", "split": "dev", "turns": list(turns)})


def test_gold_memory_carries_expected_refs_and_templates() -> None:
    conv = conversation(
        {"user": "What does Article 21 say?", "expected_type": "article_lookup",
         "expected_refs": ["21"]},
        {"user": "Punishment for theft under the BNS?", "expected_type": "out_of_scope"},
        {"user": "Does it cover self-incrimination?", "expected_type": "simple"},
    )  # fmt: skip
    memory = gold_memory(conv.turns[:2], history=6)
    assert memory.last_articles == ["21"]  # the out-of-scope turn keeps the anchor, like the API
    assert [m.role for m in memory.messages] == ["user", "assistant"] * 2
    assert memory.messages[1].content == "(answer citing Art. 21)"
    assert "only answer questions about the text" in memory.messages[3].content
    assert gold_memory(conv.turns[:2], history=2).messages[0].role == "user"


def test_turn_metrics_and_multi_turn_gate() -> None:
    conv = conversation(
        {"user": "What does Article 21 say?", "expected_type": "article_lookup",
         "expected_refs": ["21"]},
        {"user": "What are its exceptions?", "expected_type": "simple",
         "expected_standalone_contains": ["21"]},
        {"user": "Now tell me about Article 14.", "expected_type": "article_lookup",
         "expected_standalone_contains": ["14"], "expected_standalone_excludes": ["21"]},
    )  # fmt: skip
    turns = [
        TurnResult(conv, 0, RouteDecision(type="article_lookup", standalone_query="Article 21"), 5),
        TurnResult(conv, 1, RouteDecision(type="simple",
                                          standalone_query="exceptions to Article 21"), 5),
        TurnResult(conv, 2, RouteDecision(type="article_lookup",
                                          standalone_query="Article 14 vs Article 21"), 5),
    ]  # fmt: skip
    assert [t.standalone_ok for t in turns] == [None, True, False]
    metrics = aggregate_turns(turns)
    assert metrics["standalone_correctness"] == 0.5
    assert metrics["mt_type_accuracy"] == 1.0
    assert metrics["mt_conversations_all_correct"] == 0.0
    assert metrics["mt_n_standalone_turns"] == 2

    assert "standalone_correctness" not in gates_for({"type_accuracy": 1.0})
    assert gates_for(metrics) == ROUTER_GATES | {"standalone_correctness": (">=", 0.90)}


def test_repo_multi_turn_set_loads() -> None:
    from eval.golden import load_conversations

    conversations = load_conversations()
    assert len(conversations) >= 20
    assert any(len(c.turns) >= 8 for c in conversations)
    assert {c.split for c in conversations} == {"dev", "test"}
