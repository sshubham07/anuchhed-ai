"""Eval runner (spec: evaluation.md §6, retrieval §3.10).

    python -m eval.run --suite retrieval --split dev               # gated run, writes a report
    python -m eval.run --suite retrieval --split dev --ablation    # each retrieval mode
    python -m eval.run --suite router --split dev [--rpm 5]       # real router LLM, logged
                                                                  # (+ multi_turn.jsonl)

Exit code: 0 all gates pass, 1 a gate failed or regressed > 2 pts vs baseline, 2 bad usage.
"""

import argparse
import asyncio
import csv
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.golden import MULTI_TURN, GoldenCase, golden_version, load_cases
from eval.metrics import (
    best_threshold,
    hit_at_1,
    mrr_at_k,
    ndcg_at_k,
    percentile,
    recall_at_k,
)
from samvidhan.core.config import Settings, get_settings
from samvidhan.core.logging import configure_logging
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.retrieval.service import RetrievalService, create_retrieval_service
from samvidhan.retrieval.types import RETRIEVAL_MODES, RetrievalMode, RetrievalResult

REPORTS = Path(__file__).parent / "reports"
BASELINE = REPORTS / "baseline.json"
ROUTER_BASELINE = REPORTS / "baseline_router.json"
REGRESSION_TOLERANCE = 0.02  # evaluation.md §5: fail on a drop of more than 2 points

# evaluation.md §5 retrieval gates: metric → (operator, threshold)
GATES: dict[str, tuple[str, float]] = {
    "recall@5": (">=", 0.90),
    "hit@1_lookup": (">=", 1.00),
    "mrr@10": (">=", 0.75),
    "candidate_recall@15": (">=", 0.95),
}
# HLD §14 SLOs, reported but not gated (hardware-dependent)
SLOS_MS: dict[str, float] = {"retrieval_p95_ms": 300, "rerank_p95_ms": 800}

EXIT_OK, EXIT_GATE_FAILED, EXIT_USAGE = 0, 1, 2


@dataclass
class CaseResult:
    case: GoldenCase
    refs: list[str | None]  # ref per chunk: pinned + ranked, deduped
    candidate_refs: list[str | None]  # pinned + fused candidates
    top_score: float | None
    top_ref: str | None  # best non-pinned chunk (for confidence tuning)
    latency_ms: dict[str, int]
    metrics: dict[str, float] = field(default_factory=dict)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.run")
    parser.add_argument("--suite", choices=["retrieval", "router", "full"], default="retrieval")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--mode", choices=RETRIEVAL_MODES, default="hybrid_rerank")
    parser.add_argument("--ablation", action="store_true", help="run every mode, write a table")
    parser.add_argument("--out", type=Path, default=REPORTS)
    parser.add_argument(
        "--rpm", type=float, default=5, help="router suite: max calls/min (Groq free: 8K tok/min)"
    )
    parser.add_argument(
        "--multi-turn", type=Path, default=MULTI_TURN, help="router suite: conversations file"
    )
    args = parser.parse_args(argv)
    if args.ablation and args.split != "dev":
        print("--ablation is a tuning tool; run it on --split dev only (evaluation.md §2.5)")
        return EXIT_USAGE
    if args.suite == "full":
        print("--suite full lands in a later phase (P7.3)")
        return EXIT_USAGE

    settings = get_settings()
    configure_logging(settings.model_copy(update={"log_level": "WARNING"}))
    if args.suite == "router":
        return _run_router(settings, args)
    return asyncio.run(_run(settings, args))


def _run_router(settings: Settings, args: argparse.Namespace) -> int:
    from eval import router_suite  # imports eval.run helpers; loaded on demand to avoid a cycle

    if key := router_suite.missing_key(settings):
        print(f"{key} is not set in .env; the router suite calls the real router model")
        return EXIT_USAGE
    cases, conversations = router_suite.load_inputs(args)
    run = asyncio.run(router_suite.run_router_suite(settings, args, cases, conversations))
    report = run.report
    report["gates"] = _gates(
        report["metrics"],
        _load_baseline(ROUTER_BASELINE),
        router_suite.gates_for(report["metrics"]),
    )
    router_suite.write(args.out, run)
    sys.stdout.write(router_suite.markdown(report))
    failed = [g for g in report["gates"] if g["status"] != "PASS"]
    return EXIT_GATE_FAILED if failed else EXIT_OK


async def _run(settings: Settings, args: argparse.Namespace) -> int:
    cases = load_cases(split=args.split)
    engine = create_engine(settings)
    try:
        service = create_retrieval_service(settings, create_session_factory(engine))
        await service.retrieve("warm-up query about Article 21")  # load models / caches
        if args.ablation:
            await _ablation(service, settings, cases, args)
            return EXIT_OK
        results = await _run_cases(service, cases, args.mode)
    finally:
        await engine.dispose()

    report = _report(settings, args, cases, results, args.mode)
    report["gates"] = _gates(report["metrics"], _load_baseline())
    _write(args.out, report, results)
    _print_summary(report)
    failed = [g for g in report["gates"] if g["status"] != "PASS"]
    return EXIT_GATE_FAILED if failed else EXIT_OK


async def _run_cases(
    service: RetrievalService,
    cases: Sequence[GoldenCase],
    mode: RetrievalMode,
    *,
    lexical_match_all: bool = False,
) -> list[CaseResult]:
    results = []
    for case in cases:
        if not case.retrievable and case.category != "out_of_scope":
            continue  # ambiguous / chitchat / pure injection never reach retrieval
        result = await service.retrieve(
            case.question,
            refs=case.question_refs,
            mode=mode,
            lexical_match_all=lexical_match_all,
        )
        results.append(_score(case, result))
    return results


def _score(case: GoldenCase, result: RetrievalResult) -> CaseResult:
    """Metrics on pinned + ranked (as served), plus `*_unpinned` on the search legs alone.

    `ranked` never contains pinned chunks' influence (the legs ignore refs), so the unpinned
    metrics show what retrieval does when the router misses a ref. MRR@10 runs over the
    served order: context first, then the rest of the reranked candidates (spec §3.10).
    """
    pinned = [c for c in result.chunks if c.pinned]
    pinned_ids = {c.id for c in pinned}
    ordered = [*pinned, *(c for c in result.ranked if c.id not in pinned_ids)]
    unpinned = [c.ref for c in result.ranked]
    scored = CaseResult(
        case=case,
        refs=[c.ref for c in ordered],
        candidate_refs=[c.ref for c in result.candidates],  # before rerank, no pins (§3.1)
        top_score=result.top_score,
        top_ref=unpinned[0] if unpinned else None,
        latency_ms=result.latency_ms,
    )
    if case.retrievable:
        expected = case.expected_refs
        scored.metrics = {
            "recall@5": recall_at_k(scored.refs, expected, 5),
            "hit@1": hit_at_1(scored.refs, expected),
            "mrr@10": mrr_at_k(scored.refs, expected, 10),
            "ndcg@5": ndcg_at_k(scored.refs, expected, case.acceptable_refs, 5),
            "candidate_recall@15": recall_at_k(
                scored.candidate_refs, expected, len(scored.candidate_refs)
            ),
            "recall@context": recall_at_k(scored.refs, expected, len(result.chunks)),
            "recall@5_unpinned": recall_at_k(unpinned, expected, 5),
            "hit@1_unpinned": hit_at_1(unpinned, expected),
            "mrr@10_unpinned": mrr_at_k(unpinned, expected, 10),
        }
    return scored


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def _aggregate(results: Sequence[CaseResult]) -> dict[str, float]:
    scored = [r for r in results if r.metrics]
    names = ["recall@5", "mrr@10", "ndcg@5", "candidate_recall@15", "hit@1"]
    names += ["recall@5_unpinned", "mrr@10_unpinned", "hit@1_unpinned"]
    out = {name: _mean([r.metrics[name] for r in scored]) for name in names}
    lookups = [r for r in scored if r.case.category == "article_lookup"]
    out["hit@1_lookup"] = _mean([r.metrics["hit@1"] for r in lookups])
    out["hit@1_lookup_unpinned"] = _mean([r.metrics["hit@1_unpinned"] for r in lookups])
    out["n_pinned_cases"] = sum(1 for r in scored if r.case.question_refs)
    out["n_cases"] = len(scored)
    return out


def _report(
    settings: Settings,
    args: argparse.Namespace,
    cases: Sequence[GoldenCase],
    results: Sequence[CaseResult],
    mode: RetrievalMode,
) -> dict[str, Any]:
    gated = [r for r in results if r.metrics and r.case.category != "long_query"]
    long_queries = [r for r in results if r.metrics and r.case.category == "long_query"]
    by_category: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        if r.metrics:
            by_category[r.case.category].append(r)

    retrieval_ms = [float(r.latency_ms.get("retrieval", 0)) for r in results]
    rerank_ms = [float(r.latency_ms["rerank"]) for r in results if "rerank" in r.latency_ms]
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    return {
        "run_id": f"{timestamp}_retrieval_{args.split}",
        "suite": "retrieval",
        "split": args.split,
        "git_sha": _git_sha(),
        "golden_version": golden_version(),
        "config": {
            "mode": mode,
            "chunker": settings.chunker_version,
            "embed_model": settings.embed_model,
            "rerank_model": settings.rerank_model,
            "dense_k": settings.dense_k,
            "lexical_k": settings.lexical_k,
            "rrf_k": settings.rrf_k,
            "rerank_candidates": settings.rerank_candidates,
            "rerank_max_length": settings.rerank_max_length,
            "final_k": settings.final_k,
            "low_confidence_threshold": settings.low_confidence_threshold,
        },
        "n_cases_total": len(cases),
        "metrics": _aggregate(gated),
        "long_query": _aggregate(long_queries)
        | {"recall@context": _mean([r.metrics["recall@context"] for r in long_queries])},
        "by_category": {name: _aggregate(rs) for name, rs in sorted(by_category.items())},
        "latency": {
            "retrieval_p50_ms": percentile(retrieval_ms, 50),
            "retrieval_p95_ms": percentile(retrieval_ms, 95),
            "rerank_p50_ms": percentile(rerank_ms, 50),
            "rerank_p95_ms": percentile(rerank_ms, 95),
        },
        "confidence": _confidence(results),
        "failures": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "question": r.case.question,
                "expected": r.case.expected_refs,
                "got": _unique(r.refs)[:8],
                "recall@5": r.metrics["recall@5"],
                "mrr@10": round(r.metrics["mrr@10"], 3),
            }
            for r in gated
            if r.metrics["recall@5"] < 1.0 or r.metrics["mrr@10"] < 1.0
        ],
    }


def _confidence(results: Sequence[CaseResult]) -> dict[str, Any]:
    """P3.8: top rerank score of hits vs misses/out-of-scope (cases with pinned refs excluded)."""
    positives, negatives = [], []
    for r in results:
        if r.top_score is None or r.case.question_refs:
            continue
        if r.case.retrievable and r.top_ref in r.case.expected_refs:
            positives.append(r.top_score)
        else:
            negatives.append(r.top_score)
    threshold, accuracy = best_threshold(positives, negatives)

    def describe(values: list[float]) -> dict[str, float]:
        return {
            "n": len(values),
            "min": round(min(values), 4) if values else 0.0,
            "p25": round(percentile(values, 25), 4),
            "median": round(percentile(values, 50), 4),
            "max": round(max(values), 4) if values else 0.0,
        }

    return {
        "best_threshold": round(threshold, 4),
        "accuracy": round(accuracy, 4),
        "hits": describe(positives),
        "misses_and_out_of_scope": describe(negatives),
    }


def _gates(
    metrics: dict[str, float],
    baseline: dict[str, Any] | None,
    gates_spec: dict[str, tuple[str, float]] = GATES,
) -> list[dict[str, Any]]:
    gates = []
    base_metrics = (baseline or {}).get("metrics", {})
    for name, (op, threshold) in gates_spec.items():
        value = metrics.get(name, 0.0)
        base = base_metrics.get(name)
        status = "PASS" if value >= threshold else "FAIL"
        if status == "PASS" and base is not None and value < base - REGRESSION_TOLERANCE:
            status = "REGRESSED"
        gates.append(
            {
                "metric": name,
                "value": value,
                "op": op,
                "threshold": threshold,
                "baseline": base,
                "status": status,
            }  # fmt: skip
        )
    return gates


def _load_baseline(path: Path = BASELINE) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def _unique(refs: Sequence[str | None]) -> list[str]:
    seen: list[str] = []
    for ref in refs:
        if ref is not None and ref not in seen:
            seen.append(ref)
    return seen


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def _write(out: Path, report: dict[str, Any], results: Sequence[CaseResult]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stem = out / report["run_id"]
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    with (out / f"{report['run_id']}_samples.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        metric_names = ["recall@5", "hit@1", "mrr@10", "ndcg@5", "candidate_recall@15"]
        writer.writerow(["id", "category", "expected", "got", "top_score", *metric_names])
        for r in results:
            writer.writerow(
                [
                    r.case.id,
                    r.case.category,
                    " ".join(r.case.expected_refs),
                    " ".join(_unique(r.refs)[:8]),
                    "" if r.top_score is None else round(r.top_score, 4),
                    *(round(r.metrics[m], 4) if r.metrics else "" for m in metric_names),
                ]
            )


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Retrieval eval — {report['run_id']}",
        "",
        f"git `{report['git_sha']}` · golden v{report['golden_version']} · split "
        f"`{report['split']}` · mode `{report['config']['mode']}` · "
        f"{report['metrics']['n_cases']} gated cases",
        "",
        "| Metric | Value | Threshold | Baseline | Status |",
        "|--------|-------|-----------|----------|--------|",
    ]
    for g in report["gates"]:
        base = "—" if g["baseline"] is None else f"{g['baseline']:.3f}"
        lines.append(
            f"| {g['metric']} | {g['value']:.3f} | {g['op']} {g['threshold']:.2f} | {base} "
            f"| {g['status']} |"
        )
    m = report["metrics"]
    lines += [
        f"| ndcg@5 | {m['ndcg@5']:.3f} | — | — | info |",
        "",
        f"{m['n_pinned_cases']} of {m['n_cases']} cases name a ref in the question and get it "
        "pinned (a perfect router). Without pinning — the search legs alone — the same cases "
        "score:",
        "",
        "| recall@5 | mrr@10 | hit@1 | hit@1 (lookup) |",
        "|---|---|---|---|",
        f"| {m['recall@5_unpinned']:.3f} | {m['mrr@10_unpinned']:.3f} | "
        f"{m['hit@1_unpinned']:.3f} | {m['hit@1_lookup_unpinned']:.3f} |",
        "",
        "## Latency (HLD §14, not gated)",
        "",
        "| Stage | p50 ms | p95 ms | SLO p95 |",
        "|-------|--------|--------|---------|",
    ]
    lat = report["latency"]
    for stage in ("retrieval", "rerank"):
        lines.append(
            f"| {stage} | {lat[f'{stage}_p50_ms']:.0f} | {lat[f'{stage}_p95_ms']:.0f} | "
            f"{SLOS_MS[f'{stage}_p95_ms']:.0f} |"
        )
    lines += [
        "",
        "## By category",
        "",
        "| Category | n | recall@5 | mrr@10 | hit@1 | cand@15 |",
        "|----------|---|----------|--------|-------|---------|",
    ]
    for name, cm in report["by_category"].items():
        lines.append(
            f"| {name} | {cm['n_cases']} | {cm['recall@5']:.3f} | {cm['mrr@10']:.3f} | "
            f"{cm['hit@1']:.3f} | {cm['candidate_recall@15']:.3f} |"
        )
    lq = report["long_query"]
    conf = report["confidence"]
    lines += [
        "",
        f"`long_query` (not gated until P4.5): n={lq['n_cases']}, recall@5={lq['recall@5']:.3f},"
        f" recall@context={lq['recall@context']:.3f}",
        "",
        "## Confidence (P3.8)",
        "",
        f"Best threshold **{conf['best_threshold']}** (accuracy {conf['accuracy']}). "
        f"Hits: {conf['hits']} · misses/out-of-scope: {conf['misses_and_out_of_scope']}",
        "",
        "## Failures",
        "",
    ]
    for f in report["failures"]:
        lines.append(
            f"- **{f['id']}** ({f['category']}) recall@5={f['recall@5']:.2f} "
            f"mrr={f['mrr@10']:.2f} — {f['question'][:90]} — expected {f['expected']}, "
            f"got {f['got']}"
        )
    return "\n".join(lines) + "\n"


def _print_summary(report: dict[str, Any]) -> None:
    sys.stdout.write(_markdown(report))


async def _ablation(
    service: RetrievalService,
    settings: Settings,
    cases: Sequence[GoldenCase],
    args: argparse.Namespace,
) -> None:
    """P3.7: the same cases through each mode (+ lexical AND vs OR); writes ablation_v1.md."""
    variants: list[tuple[str, RetrievalMode, bool]] = [
        ("dense only", "dense", False),
        ("lexical only (AND, HLD original)", "lexical", True),
        ("lexical only (OR)", "lexical", False),
        ("hybrid (RRF, OR)", "hybrid", False),
        ("hybrid + rerank", "hybrid_rerank", False),
    ]
    rows = []
    for label, mode, match_all in variants:
        results = await _run_cases(service, cases, mode, lexical_match_all=match_all)
        report = _report(settings, args, cases, results, mode)
        rows.append((label, report))
        sys.stdout.write(f"{label}: {report['metrics']}\n")

    git = rows[0][1]["git_sha"]
    lines = [
        "# Retrieval ablation v1 (P3.7)",
        "",
        f"git `{git}` · golden v{golden_version()} · split `{args.split}` · "
        f"{rows[0][1]['metrics']['n_cases']} gated cases (long_query excluded) · "
        f"generated {datetime.now(UTC):%Y-%m-%d}",
        "",
        "Hit@1 (lookup) is via pinning in every row; the unpinned column is the search legs alone.",
        "",
        "| Variant | Recall@5 | MRR@10 | nDCG@5 | Hit@1 (lookup) | Hit@1 lookup unpinned | "
        "Recall@5 unpinned | Cand. recall@15 | retrieval p95 ms | rerank p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, report in rows:
        m, lat = report["metrics"], report["latency"]
        rerank = f"{lat['rerank_p95_ms']:.0f}" if lat["rerank_p95_ms"] else "—"
        lines.append(
            f"| {label} | {m['recall@5']:.3f} | {m['mrr@10']:.3f} | {m['ndcg@5']:.3f} | "
            f"{m['hit@1_lookup']:.3f} | {m['hit@1_lookup_unpinned']:.3f} | "
            f"{m['recall@5_unpinned']:.3f} | {m['candidate_recall@15']:.3f} | "
            f"{lat['retrieval_p95_ms']:.0f} | {rerank} |"
        )
    lines.append("")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "ablation_v1.md").write_text("\n".join(lines), encoding="utf-8")
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
