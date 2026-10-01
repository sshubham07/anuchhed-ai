"""Router eval suite (spec: llm-router-generation §3.11, evaluation.md §3.2, §5).

    python -m eval.run --suite router --split dev [--rpm 25]

Every single-turn case goes through the real router (`LLMClient`, every call logged to
`llm_calls`) with an empty memory. Multi-turn conversations (`multi_turn.jsonl`) replay each turn
with memory built from the *expected* earlier turns (spec: api-sessions-memory §9.2), so one miss
doesn't cascade; standalone correctness is gated at 0.90.
"""

import argparse
import asyncio
import csv
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.golden import (
    GoldenCase,
    GoldenConversation,
    GoldenTurn,
    golden_version,
    load_cases,
    load_conversations,
)
from eval.metrics import percentile, set_f1
from samvidhan.core.config import Settings
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.generation import templates
from samvidhan.generation.citations import label
from samvidhan.llm.client import LLMClient
from samvidhan.llm.factory import create_llm_client
from samvidhan.llm.prompts import load_prompt
from samvidhan.llm.types import provider_of
from samvidhan.memory.structured import apply_turn
from samvidhan.memory.types import SessionMemory, Turn
from samvidhan.query.router import ROUTE_TYPES, RouteDecision, route
from samvidhan.retrieval.lookup import normalize_refs

# evaluation.md §5, router/prompt PR gates: metric → (operator, threshold)
ROUTER_GATES: dict[str, tuple[str, float]] = {
    "type_accuracy": (">=", 0.90),
    "refs_f1": (">=", 0.95),
    "json_validity": (">=", 0.99),
    "answer_style_accuracy": (">=", 0.85),
}
MULTI_TURN_GATES: dict[str, tuple[str, float]] = {"standalone_correctness": (">=", 0.90)}


def gates_for(metrics: dict[str, float]) -> dict[str, tuple[str, float]]:
    """The multi-turn gate applies only when conversations were run."""
    return ROUTER_GATES | (MULTI_TURN_GATES if "standalone_correctness" in metrics else {})


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


def contains_token(text: str, token: str) -> bool:
    """Case-insensitive. Tokens with a digit match whole words ("21" matches "Article 21", not
    "210" or "21A"); words match from a word start, so "Panchayat" also matches "Panchayats"."""
    end = r"(?!\w)" if any(ch.isdigit() for ch in token) else ""
    return re.search(rf"(?<!\w){re.escape(token)}{end}", text, re.IGNORECASE) is not None


@dataclass
class TurnResult:
    conversation: GoldenConversation
    index: int  # 0-based turn number
    decision: RouteDecision
    latency_ms: int

    @property
    def turn(self) -> GoldenTurn:
        return self.conversation.turns[self.index]

    @property
    def type_ok(self) -> bool:
        return self.decision.type == self.turn.expected_type

    @property
    def style_ok(self) -> bool:
        return self.decision.answer_style == self.turn.expected_answer_style

    @property
    def standalone_ok(self) -> bool | None:
        """None for turns without standalone expectations (usually the first)."""
        if not self.turn.checks_standalone:
            return None
        query = self.decision.standalone_query
        return all(contains_token(query, t) for t in self.turn.expected_standalone_contains) and (
            not any(contains_token(query, t) for t in self.turn.expected_standalone_excludes)
        )

    @property
    def ok(self) -> bool:
        return (
            self.type_ok
            and self.style_ok
            and self.standalone_ok is not False
            and not self.decision.fallback
        )


def placeholder_answer(turn: GoldenTurn) -> str:
    """What the assistant said on an expected earlier turn: the real template for template
    routes, otherwise a stand-in naming the Articles a correct answer cites."""
    if turn.expected_type == "ambiguous":
        return templates.clarification(None)
    if turn.expected_type == "out_of_scope":
        return templates.OUT_OF_SCOPE
    if turn.expected_type == "chitchat":
        return templates.chitchat(turn.user)
    refs = ", ".join(label(ref) for ref in turn.expected_refs)
    return f"(answer citing {refs})" if refs else "(answer)"


def gold_memory(turns: Sequence[GoldenTurn], history: int) -> SessionMemory:
    """Memory after the expected `turns`, built the way the API builds it (`apply_turn`)."""
    memory = SessionMemory()
    for turn in turns:
        memory = apply_turn(
            memory,
            Turn(
                user=turn.user,
                assistant=placeholder_answer(turn),
                standalone_query=turn.user,
                cited_refs=list(turn.expected_refs),
                route_type=turn.expected_type,
            ),
            max_messages=history,
        )
    return memory


def missing_key(settings: Settings) -> str | None:
    """The env var the router model needs but isn't set, if any."""
    keys = {"groq": settings.groq_api_key, "gemini": settings.gemini_api_key}
    provider = provider_of(settings.router_model)
    if provider in keys and not keys[provider].get_secret_value():
        return f"{provider.upper()}_API_KEY"
    return None


@dataclass
class RouterRun:
    report: dict[str, Any]
    results: list[RouterCaseResult]
    turns: list[TurnResult]


def load_inputs(args: argparse.Namespace) -> tuple[list[GoldenCase], list[GoldenConversation]]:
    """Single-turn cases and conversations of the split; conversations are optional."""
    multi_turn: Path = args.multi_turn
    if not multi_turn.exists():
        sys.stdout.write(f"{multi_turn} not found; skipping multi-turn conversations\n")
        return load_cases(split=args.split), []
    return load_cases(split=args.split), load_conversations(multi_turn, split=args.split)


async def run_router_suite(
    settings: Settings,
    args: argparse.Namespace,
    cases: Sequence[GoldenCase],
    conversations: Sequence[GoldenConversation],
) -> RouterRun:
    engine = create_engine(settings)
    llm = create_llm_client(settings, create_session_factory(engine))
    pace = _Pacer(args.rpm)
    try:
        results = await _run_cases(llm, settings, cases, pace)
        turns = await _run_conversations(llm, settings, conversations, pace)
    finally:
        await llm.recorder.drain()  # every row lands, even if a case raised
        await engine.dispose()
    return RouterRun(
        report(settings, args.split, cases, results, conversations, turns), results, turns
    )


class _Pacer:
    """Keeps router calls under `rpm` for free-tier limits."""

    def __init__(self, rpm: float) -> None:
        self._interval = 60.0 / rpm if rpm > 0 else 0.0

    async def wait(self, started: float) -> None:
        await asyncio.sleep(max(0.0, self._interval - (time.perf_counter() - started)))


async def _run_cases(
    llm: LLMClient, settings: Settings, cases: Sequence[GoldenCase], pace: _Pacer
) -> list[RouterCaseResult]:
    prompt = load_prompt(settings.prompts_dir, settings.router_prompt_version)
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
        await pace.wait(started)
    return results


async def _run_conversations(
    llm: LLMClient,
    settings: Settings,
    conversations: Sequence[GoldenConversation],
    pace: _Pacer,
) -> list[TurnResult]:
    prompt = load_prompt(settings.prompts_dir, settings.router_prompt_version)
    results: list[TurnResult] = []
    for n, conversation in enumerate(conversations, start=1):
        for index, turn in enumerate(conversation.turns):
            memory = gold_memory(conversation.turns[:index], settings.router_history_messages)
            started = time.perf_counter()
            decision = await route(llm, settings, prompt, turn.user, memory)
            latency_ms = round((time.perf_counter() - started) * 1000)
            results.append(TurnResult(conversation, index, decision, latency_ms))
            mark = "✓" if results[-1].ok else "✗"
            sys.stdout.write(
                f"[MT {n}/{len(conversations)}] {mark} {conversation.id}#{index + 1} "
                f"{turn.expected_type} → {decision.type} · {decision.standalone_query[:70]}\n"
            )
            await pace.wait(started)
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


def aggregate_turns(turns: Sequence[TurnResult]) -> dict[str, float]:
    def mean(values: Sequence[float]) -> float:
        return round(statistics.fmean(values), 4) if values else 0.0

    checked = [t.standalone_ok for t in turns if t.standalone_ok is not None]
    by_conversation: dict[str, list[TurnResult]] = defaultdict(list)
    for t in turns:
        by_conversation[t.conversation.id].append(t)
    return {
        "standalone_correctness": mean([float(ok) for ok in checked]),
        "mt_type_accuracy": mean([float(t.type_ok) for t in turns]),
        "mt_answer_style_accuracy": mean([float(t.style_ok) for t in turns]),
        "mt_json_validity": mean([float(not t.decision.fallback) for t in turns]),
        "mt_conversations_all_correct": mean(
            [float(all(t.ok for t in ts)) for ts in by_conversation.values()]
        ),
        "mt_n_conversations": len(by_conversation),
        "mt_n_turns": len(turns),
        "mt_n_standalone_turns": len(checked),
    }


def report(
    settings: Settings,
    split: str,
    cases: Sequence[GoldenCase],
    results: Sequence[RouterCaseResult],
    conversations: Sequence[GoldenConversation] = (),
    turns: Sequence[TurnResult] = (),
) -> dict[str, Any]:
    from eval.run import _git_sha  # run.py imports this module lazily; no cycle at import time

    by_category: dict[str, list[RouterCaseResult]] = defaultdict(list)
    for r in results:
        by_category[r.case.category].append(r)
    confusion = Counter((r.case.expected_type, r.decision.type) for r in results)
    latencies = [float(r.latency_ms) for r in results] + [float(t.latency_ms) for t in turns]
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
        "n_conversations_total": len(conversations),
        "metrics": aggregate(results) | (aggregate_turns(turns) if turns else {}),
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
        "multi_turn_failures": [
            {
                "id": f"{t.conversation.id}#{t.index + 1}",
                "user": t.turn.user,
                "expected_type": t.turn.expected_type,
                "got_type": t.decision.type,
                "expected_style": t.turn.expected_answer_style,
                "got_style": t.decision.answer_style,
                "standalone_query": t.decision.standalone_query,
                "standalone_ok": t.standalone_ok,
                "expected_contains": t.turn.expected_standalone_contains,
                "expected_excludes": t.turn.expected_standalone_excludes,
                "fallback": t.decision.fallback,
            }
            for t in turns
            if not t.ok
        ],
    }


def write(out: Path, run: RouterRun) -> None:
    report_, results = run.report, run.results
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
    if run.turns:
        _write_turns(out / f"{report_['run_id']}_multi_turn_samples.csv", run.turns)


def _write_turns(path: Path, turns: Sequence[TurnResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["id", "turn", "category", "user", "expected_type", "got_type", "expected_style",
             "got_style", "standalone_ok", "expected_contains", "expected_excludes", "fallback",
             "latency_ms", "standalone_query"]
        )  # fmt: skip
        for t in turns:
            writer.writerow(
                [
                    t.conversation.id,
                    t.index + 1,
                    t.conversation.category,
                    t.turn.user,
                    t.turn.expected_type,
                    t.decision.type,
                    t.turn.expected_answer_style,
                    t.decision.answer_style,
                    "" if t.standalone_ok is None else t.standalone_ok,
                    " | ".join(t.turn.expected_standalone_contains),
                    " | ".join(t.turn.expected_standalone_excludes),
                    t.decision.fallback,
                    t.latency_ms,
                    t.decision.standalone_query,
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
    if "standalone_correctness" in m:
        lines += [
            "",
            "## Multi-turn (gold history)",
            "",
            f"{m['mt_n_conversations']} conversations · {m['mt_n_turns']} turns · "
            f"{m['mt_n_standalone_turns']} with standalone checks",
            "",
            "| Metric | Value |",
            "|--------|-------|",
        ]
        for key in ("standalone_correctness", "mt_type_accuracy", "mt_answer_style_accuracy",
                    "mt_json_validity", "mt_conversations_all_correct"):  # fmt: skip
            lines.append(f"| {key} | {m[key]:.3f} |")
        lines += ["", "### Multi-turn failures", ""]
        for f in report_.get("multi_turn_failures", []):
            standalone = {None: "—", True: "ok", False: "MISS"}[f["standalone_ok"]]
            lines.append(
                f"- **{f['id']}** type {f['expected_type']}→{f['got_type']}, style "
                f"{f['expected_style']}→{f['got_style']}, standalone {standalone} "
                f"(want {f['expected_contains']}, not {f['expected_excludes']})"
                f"{' · FALLBACK' if f['fallback'] else ''} — {f['user'][:70]} → "
                f"_{f['standalone_query'][:100]}_"
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
