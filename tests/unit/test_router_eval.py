"""Router eval metrics and golden validation (spec §3.11, evaluation.md §3.2)."""

import json
from pathlib import Path

import pytest

from eval.golden import GoldenCase, load_cases
from eval.metrics import set_f1
from eval.router_suite import RouterCaseResult, aggregate, markdown
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
    assert (Path("eval/golden/VERSION").read_text().strip()) == "0.2"
