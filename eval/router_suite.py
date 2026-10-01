"""Router eval suite (spec: llm-router-generation §3.11, evaluation.md §3.2, §5).

    python -m eval.run --suite router --split dev [--rpm 25]

Every single-turn case goes through the real router (`LLMClient`, every call logged to
`llm_calls`) with an empty memory. Multi-turn standalone correctness comes in Phase 5 (P5.10).
"""

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.golden import GoldenCase, golden_version, load_cases
from eval.metrics import percentile, set_f1
from samvidhan.core.config import Settings
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.llm.client import LLMClient
from samvidhan.llm.factory import create_llm_client
from samvidhan.llm.prompts import load_prompt
from samvidhan.llm.types import provider_of
from samvidhan.memory.types import SessionMemory
from samvidhan.query.router import ROUTE_TYPES, RouteDecision, route
from samvidhan.retrieval.lookup import normalize_refs

# evaluation.md §5, router/prompt PR gates: metric → (operator, threshold)
ROUTER_GATES: dict[str, tuple[str, float]] = {
    "type_accuracy": (">=", 0.90),
    "refs_f1": (">=", 0.95),
    "json_validity": (">=", 0.99),
    "answer_style_accuracy": (">=", 0.85),
}


@dataclass
class RouterCaseResult:
    case: GoldenCase
    decision: RouteDecision
    refs: list[str]  # normalized article_refs + schedule_refs
    latency_ms: int

    @property
    def type_ok(self) -> bool:
        return self.decision.type == self.case.expected_type

    @property
    def refs_f1(self) -> float:
        return set_f1(self.refs, self.case.question_refs)

    @property
    def style_ok(self) -> bool:
        return self.decision.answer_style == self.case.expected_answer_style


def missing_key(settings: Settings) -> str | None:
    """The env var the router model needs but isn't set, if any."""
    keys = {"groq": settings.groq_api_key, "gemini": settings.gemini_api_key}
    provider = provider_of(settings.router_model)
    if provider in keys and not keys[provider].get_secret_value():
        return f"{provider.upper()}_API_KEY"
    return None


async def run_router_suite(
    settings: Settings, args: argparse.Namespace
) -> tuple[dict[str, Any], list[RouterCaseResult]]:
    cases = load_cases(split=args.split)
    engine = create_engine(settings)
    llm = create_llm_client(settings, create_session_factory(engine))
    try:
        results = await _run_cases(llm, settings, cases, rpm=args.rpm)
    finally:
        await llm.recorder.drain()  # every row lands, even if a case raised
        await engine.dispose()
    return report(settings, args.split, cases, results), results


async def _run_cases(
    llm: LLMClient, settings: Settings, cases: Sequence[GoldenCase], *, rpm: float
) -> list[RouterCaseResult]:
    prompt = load_prompt(settings.prompts_dir, settings.router_prompt_version)
    interval = 60.0 / rpm if rpm > 0 else 0.0
    results: list[RouterCaseResult] = []
    for n, case in enumerate(cases, start=1):
        started = time.perf_counter()
        decision = await route(llm, settings, prompt, case.question, SessionMemory())
        latency_ms = round((time.perf_counter() - started) * 1000)
        refs, _ = normalize_refs(decision.refs)
        results.append(RouterCaseResult(case, decision, refs, latency_ms))
        mark = "✓" if results[-1].type_ok else "✗"
        sys.stdout.write(
            f"[{n}/{len(cases)}] {mark} {case.id} {case.expected_type} → {decision.type}\n"
        )
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - started)))
    return results


def aggregate(results: Sequence[RouterCaseResult]) -> dict[str, float]:
    def mean(values: Sequence[float]) -> float:
        return round(statistics.fmean(values), 4) if values else 0.0

    with_refs = [r for r in results if r.case.question_refs]
    return {
        "type_accuracy": mean([float(r.type_ok) for r in results]),
        "refs_f1": mean([r.refs_f1 for r in results]),
        "refs_f1_with_refs": mean([r.refs_f1 for r in with_refs]),
        "json_validity": mean([float(not r.decision.fallback) for r in results]),
        "answer_style_accuracy": mean([float(r.style_ok) for r in results]),
        "n_cases": len(results),
        "n_cases_with_refs": len(with_refs),
    }


def report(
    settings: Settings,
    split: str,
    cases: Sequence[GoldenCase],
    results: Sequence[RouterCaseResult],
) -> dict[str, Any]:
    from eval.run import _git_sha  # run.py imports this module lazily; no cycle at import time

    by_category: dict[str, list[RouterCaseResult]] = defaultdict(list)
    for r in results:
        by_category[r.case.category].append(r)
    confusion = Counter((r.case.expected_type, r.decision.type) for r in results)
    latencies = [float(r.latency_ms) for r in results]
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    return {
        "run_id": f"{timestamp}_router_{split}",
        "suite": "router",
        "split": split,
        "git_sha": _git_sha(),
        "golden_version": golden_version(),
        "config": {
            "router_model": settings.router_model,
            "router_model_long": settings.router_model_long,
            "router_fallback_model": settings.router_fallback_model,
            "router_prompt": settings.router_prompt_version,
            "long_query_chars": settings.long_query_chars,
        },
        "n_cases_total": len(cases),
        "metrics": aggregate(results),
        "by_category": {name: aggregate(rs) for name, rs in sorted(by_category.items())},
        "confusion": [{"expected": e, "got": g, "n": n} for (e, g), n in sorted(confusion.items())],
        "latency": {"p50_ms": percentile(latencies, 50), "p95_ms": percentile(latencies, 95)},
        "failures": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "question": r.case.question,
                "expected_type": r.case.expected_type,
                "got_type": r.decision.type,
                "expected_refs": r.case.question_refs,
                "got_refs": r.refs,
                "expected_style": r.case.expected_answer_style,
                "got_style": r.decision.answer_style,
                "fallback": r.decision.fallback,
                "reason": r.decision.reason,
            }
            for r in results
            if not (r.type_ok and r.refs_f1 == 1.0 and r.style_ok and not r.decision.fallback)
        ],
    }


def write(out: Path, report_: dict[str, Any], results: Sequence[RouterCaseResult]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stem = out / report_["run_id"]
    stem.with_suffix(".json").write_text(json.dumps(report_, indent=2) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(markdown(report_), encoding="utf-8")
    with (out / f"{report_['run_id']}_samples.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["id", "category", "expected_type", "got_type", "expected_refs", "got_refs",
             "refs_f1", "expected_style", "got_style", "fallback", "latency_ms", "standalone_query"]
        )  # fmt: skip
        for r in results:
            writer.writerow(
                [
                    r.case.id,
                    r.case.category,
                    r.case.expected_type,
                    r.decision.type,
                    " ".join(r.case.question_refs),
                    " ".join(r.refs),
                    round(r.refs_f1, 3),
                    r.case.expected_answer_style,
                    r.decision.answer_style,
                    r.decision.fallback,
                    r.latency_ms,
                    r.decision.standalone_query,
                ]
            )


def markdown(report_: dict[str, Any]) -> str:
    m = report_["metrics"]
    lines = [
        f"# Router eval — {report_['run_id']}",
        "",
        f"git `{report_['git_sha']}` · golden v{report_['golden_version']} · split "
        f"`{report_['split']}` · router `{report_['config']['router_model']}` "
        f"(long: `{report_['config']['router_model_long']}`) · prompt "
        f"`{report_['config']['router_prompt']}` · {m['n_cases']} cases",
        "",
        "| Metric | Value | Threshold | Baseline | Status |",
        "|--------|-------|-----------|----------|--------|",
    ]
    for g in report_["gates"]:
        base = "—" if g["baseline"] is None else f"{g['baseline']:.3f}"
        lines.append(
            f"| {g['metric']} | {g['value']:.3f} | {g['op']} {g['threshold']:.2f} | {base} "
            f"| {g['status']} |"
        )
    lines += [
        f"| refs_f1 (cases naming a ref, n={m['n_cases_with_refs']}) | "
        f"{m['refs_f1_with_refs']:.3f} | — | — | info |",
        "",
        f"Latency p50 {report_['latency']['p50_ms']:.0f} ms · p95 "
        f"{report_['latency']['p95_ms']:.0f} ms (HLD §14 router SLO p95 ≤ 900 ms)",
        "",
        "## Confusion (expected → got)",
        "",
        "| expected \\ got | " + " | ".join(ROUTE_TYPES) + " |",
        "|---|" + "---|" * len(ROUTE_TYPES),
    ]
    counts = {(c["expected"], c["got"]): c["n"] for c in report_["confusion"]}
    for expected in ROUTE_TYPES:
        row = [str(counts.get((expected, got), "")) for got in ROUTE_TYPES]
        if any(row):
            lines.append(f"| {expected} | " + " | ".join(row) + " |")
    lines += [
        "",
        "## By category",
        "",
        "| Category | n | type acc | refs F1 | style acc |",
        "|----------|---|----------|---------|-----------|",
    ]
    for name, cm in report_["by_category"].items():
        lines.append(
            f"| {name} | {cm['n_cases']} | {cm['type_accuracy']:.3f} | {cm['refs_f1']:.3f} | "
            f"{cm['answer_style_accuracy']:.3f} |"
        )
    lines += ["", "## Failures", ""]
    for f in report_["failures"]:
        lines.append(
            f"- **{f['id']}** ({f['category']}) type {f['expected_type']}→{f['got_type']}, "
            f"refs {f['expected_refs']}→{f['got_refs']}, style "
            f"{f['expected_style']}→{f['got_style']}{' · FALLBACK' if f['fallback'] else ''} — "
            f"{f['question'][:90]} — _{f['reason'][:80]}_"
        )
    return "\n".join(lines) + "\n"
