"""Retrieval (evaluation.md §3.1) and router (§3.2) metrics. Pure functions over ref lists.

Input is the ref of each ranked chunk (`21`, `21`, `SCH-7`, …): sub-chunks of one Article share a
ref. Cut-offs (`k`) apply to chunk positions; a ref counts once, at its first position.
"""

import math
from collections.abc import Sequence


def first_positions(refs: Sequence[str | None]) -> dict[str, int]:
    """Ref → 1-based position of its first chunk."""
    positions: dict[str, int] = {}
    for position, ref in enumerate(refs, start=1):
        if ref is not None and ref not in positions:
            positions[ref] = position
    return positions


def recall_at_k(refs: Sequence[str | None], expected: Sequence[str], k: int) -> float:
    if not expected:
        return 0.0
    found = set(r for r in refs[:k] if r is not None)
    return sum(1 for ref in set(expected) if ref in found) / len(set(expected))


def hit_at_1(refs: Sequence[str | None], expected: Sequence[str]) -> float:
    return 1.0 if refs and refs[0] in set(expected) else 0.0


def mrr_at_k(refs: Sequence[str | None], expected: Sequence[str], k: int) -> float:
    wanted = set(expected)
    for position, ref in enumerate(refs[:k], start=1):
        if ref in wanted:
            return 1.0 / position
    return 0.0


def ndcg_at_k(
    refs: Sequence[str | None], expected: Sequence[str], acceptable: Sequence[str], k: int
) -> float:
    """Graded: expected = 2, acceptable = 1; each ref gains once, at its first position."""
    gains = {ref: 1.0 for ref in acceptable} | {ref: 2.0 for ref in expected}
    dcg = sum(
        gains.get(ref, 0.0) / math.log2(position + 1)
        for ref, position in first_positions(refs[:k]).items()
    )
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum(gain / math.log2(position + 1) for position, gain in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def set_f1(predicted: Sequence[str], expected: Sequence[str]) -> float:
    """F1 between two ref sets; both empty is a perfect 1.0 (nothing to extract, none invented)."""
    got, want = set(predicted), set(expected)
    if not got and not want:
        return 1.0
    overlap = len(got & want)
    if not overlap:
        return 0.0
    precision, recall = overlap / len(got), overlap / len(want)
    return 2 * precision * recall / (precision + recall)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (0 for an empty list)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return float(ordered[index])


def best_threshold(positives: Sequence[float], negatives: Sequence[float]) -> tuple[float, float]:
    """Threshold t (predict confident when score ≥ t) with the best accuracy; ties → lower t."""
    scores = sorted({*positives, *negatives})
    if not scores:
        return 0.0, 0.0
    candidates = [scores[0], *((a + b) / 2 for a, b in zip(scores, scores[1:], strict=False))]
    candidates.append(scores[-1] + 1e-6)
    total = len(positives) + len(negatives)

    def accuracy(t: float) -> float:
        hits = sum(s >= t for s in positives) + sum(s < t for s in negatives)
        return hits / total

    best = max(candidates, key=lambda t: (accuracy(t), -t))
    return best, accuracy(best)
