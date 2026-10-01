"""RRF fusion (spec: retrieval §3.5)."""

import pytest

from samvidhan.retrieval.fusion import rrf
from tests.unit.retrieval_helpers import chunk, leg

A, B, C, D = (chunk(x) for x in "abcd")


def test_scores_match_hand_computation() -> None:
    fused = rrf({"dense": leg("dense", A, B, C), "lexical": leg("lexical", B, D)}, k=60, limit=10)
    scores = {c.id: c.scores["rrf"] for c in fused}
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["d"] == pytest.approx(1 / 62)
    assert [c.id for c in fused] == ["b", "a", "d", "c"]


def test_keeps_per_leg_ranks_and_sets_rrf_rank() -> None:
    fused = rrf({"dense": leg("dense", A, B), "lexical": leg("lexical", B, A)}, k=60, limit=10)
    by_id = {c.id: c for c in fused}
    assert by_id["a"].ranks == {"dense": 1, "lexical": 2, "rrf": by_id["a"].ranks["rrf"]}
    assert by_id["b"].ranks["dense"] == 2 and by_id["b"].ranks["lexical"] == 1


def test_ties_break_by_best_rank_then_id() -> None:
    # a and b tie on fused score (rank 1 + rank 2 each); c and d tie on a single rank 3
    fused = rrf(
        {"dense": leg("dense", B, A, D), "lexical": leg("lexical", A, B, C)}, k=60, limit=10
    )
    assert [c.id for c in fused] == ["a", "b", "c", "d"]


def test_limit_single_leg_and_empty() -> None:
    assert [c.id for c in rrf({"dense": leg("dense", A, B, C)}, k=60, limit=2)] == ["a", "b"]
    assert rrf({}, k=60, limit=5) == []
    assert rrf({"dense": []}, k=60, limit=5) == []
