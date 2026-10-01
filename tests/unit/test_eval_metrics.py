"""Retrieval metrics on toy data (spec: evaluation.md §3.1)."""

import math

import pytest

from eval.metrics import (
    best_threshold,
    hit_at_1,
    mrr_at_k,
    ndcg_at_k,
    percentile,
    recall_at_k,
)

RANKED = ["22", "22", "21", "SCH-7", None, "14"]


def test_recall_counts_refs_once_within_k_chunks() -> None:
    assert recall_at_k(RANKED, ["22", "21"], 3) == 1.0
    assert recall_at_k(RANKED, ["22", "14"], 5) == 0.5
    assert recall_at_k(RANKED, ["14"], 6) == 1.0
    assert recall_at_k(RANKED, [], 5) == 0.0


def test_hit_at_1_and_mrr() -> None:
    assert hit_at_1(RANKED, ["22"]) == 1.0
    assert hit_at_1(RANKED, ["21"]) == 0.0
    assert hit_at_1([], ["21"]) == 0.0
    assert mrr_at_k(RANKED, ["21"], 10) == pytest.approx(1 / 3)
    assert mrr_at_k(RANKED, ["14"], 5) == 0.0
    assert mrr_at_k(RANKED, ["SCH-7", "21"], 10) == pytest.approx(1 / 3)


def test_ndcg_graded_and_first_position_only() -> None:
    # expected 21 at position 3, acceptable 22 at position 1 (its second chunk adds nothing)
    dcg = 1 / math.log2(2) + 2 / math.log2(4)
    idcg = 2 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(RANKED, ["21"], ["22"], 5) == pytest.approx(dcg / idcg)
    assert ndcg_at_k(["21", "22"], ["21"], ["22"], 5) == pytest.approx(1.0)
    assert ndcg_at_k(RANKED, [], [], 5) == 0.0


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 21)]
    assert percentile(values, 50) == 10.0
    assert percentile(values, 95) == 19.0
    assert percentile([], 95) == 0.0


def test_best_threshold_separates_scores() -> None:
    threshold, accuracy = best_threshold([0.8, 0.9, 0.6], [0.1, 0.2])
    assert 0.2 < threshold <= 0.6 and accuracy == 1.0
    assert best_threshold([], []) == (0.0, 0.0)
