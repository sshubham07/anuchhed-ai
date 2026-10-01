"""Manual retrieval check (spec: retrieval §3.2).

    python -m samvidhan.retrieval.cli search "Can police arrest me without telling me why?"
    python -m samvidhan.retrieval.cli search "explain art. 21-A" --refs 21-A --mode hybrid

Prints the final context with per-stage ranks; `--candidates` also prints the fused list.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from samvidhan.core.config import Settings, get_settings
from samvidhan.core.errors import SamvidhanError
from samvidhan.core.logging import configure_logging, get_logger
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.retrieval.service import RetrievalService, create_retrieval_service
from samvidhan.retrieval.types import RETRIEVAL_MODES, ScoredChunk

log = get_logger(__name__)

_STAGES = ("dense", "lexical", "rrf", "rerank")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    try:
        asyncio.run(_search(settings, args))
    except SamvidhanError as exc:
        log.error("retrieval_cli_failed", error=exc.message)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="samvidhan.retrieval.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="run retrieval for one or more queries")
    search.add_argument("queries", nargs="+")
    search.add_argument("--refs", default="", help="comma-separated refs to pin, e.g. 21A,SCH-7")
    search.add_argument("--mode", choices=RETRIEVAL_MODES, default="hybrid_rerank")
    search.add_argument("--candidates", action="store_true", help="also print fused candidates")
    return parser


async def _search(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_engine(settings)
    try:
        service = create_retrieval_service(settings, create_session_factory(engine))
        refs = [r for r in args.refs.split(",") if r.strip()]
        for query in args.queries:
            await _print_one(service, query, refs, args)
    finally:
        await engine.dispose()


async def _print_one(
    service: RetrievalService, query: str, refs: list[str], args: argparse.Namespace
) -> None:
    result = await service.retrieve(query, refs=refs, mode=args.mode)
    out = sys.stdout
    out.write(f"\nQ: {query}\n")
    out.write(
        f"   mode={args.mode} refs={result.refs} top_score={result.top_score} "
        f"low_confidence={result.low_confidence} latency_ms={result.latency_ms}\n"
    )
    _table("context", result.chunks)
    if args.candidates:
        _table("candidates", result.candidates)


def _table(title: str, chunks: Sequence[ScoredChunk]) -> None:
    write = sys.stdout.write
    write(f"   {title}:\n")
    write(f"   {'#':>2}  {'chunk':<18} {'ref':<9} {'pin':<3} " +
          " ".join(f"{s:>7}" for s in _STAGES) + "  score   title\n")  # fmt: skip
    for n, chunk in enumerate(chunks, start=1):
        ranks = " ".join(f"{chunk.ranks.get(s, '-'):>7}" for s in _STAGES)
        title = (chunk.article_title or chunk.embed_text.split("\n", 1)[0])[:48]
        write(
            f"   {n:>2}  {chunk.id:<18} {chunk.ref or '-':<9} {'*' if chunk.pinned else '':<3} "
            f"{ranks}  {chunk.score:6.3f}  {title}\n"
        )


if __name__ == "__main__":
    sys.exit(main())
