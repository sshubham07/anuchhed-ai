"""Ingestion CLI (spec: ingestion §3.9).

    python -m samvidhan.ingestion.cli ingest data/raw/constitution.pdf [--activate] [--dry-run]
    python -m samvidhan.ingestion.cli activate <document_id>

Exit codes: 0 ok / already ingested, 1 validation failed, 2 bad input.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from samvidhan.core.config import Settings, get_settings
from samvidhan.core.errors import InvalidSourceError
from samvidhan.core.logging import configure_logging, get_logger
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.db.models import EMBEDDING_DIM
from samvidhan.db.repositories.corpus import CorpusRepository, NewDocument
from samvidhan.ingestion.contents import load_fixture
from samvidhan.ingestion.embed import BgeM3Embedder, Embedder, TokenizerCounter
from samvidhan.ingestion.pipeline import PipelineResult, run_pipeline

DOCUMENT_TITLE = "The Constitution of India"
DEFAULT_SOURCE_URL = "https://legislative.gov.in/constitution-of-india/"
EXPECTED_ARTICLES = Path("eval/fixtures/expected_articles.txt")

EXIT_OK, EXIT_VALIDATION, EXIT_BAD_INPUT = 0, 1, 2

log = get_logger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    try:
        if args.command == "activate":
            asyncio.run(_activate(settings, uuid.UUID(args.document_id)))
            return EXIT_OK
        return asyncio.run(_ingest(settings, args))
    except InvalidSourceError as exc:
        log.error("ingestion_invalid_source", error=exc.message)
        return EXIT_BAD_INPUT
    except (ValueError, LookupError) as exc:  # bad document id / document not found
        log.error("ingestion_bad_input", error=str(exc))
        return EXIT_BAD_INPUT
    except SQLAlchemyError:
        log.error("ingestion_database_error", exc_info=True)
        return EXIT_BAD_INPUT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="samvidhan.ingestion.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="ingest the Constitution PDF")
    ingest.add_argument("pdf", type=Path)
    ingest.add_argument("--version-date", type=date.fromisoformat, default=None)
    ingest.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    ingest.add_argument("--activate", action="store_true", help="make it the active document")
    ingest.add_argument("--dry-run", action="store_true", help="no embeddings, no database")
    ingest.add_argument("--out", type=Path, default=Path("data/processed"))
    ingest.add_argument("--expected", type=Path, default=EXPECTED_ARTICLES)
    activate = sub.add_parser("activate", help="make an ingested document the active one")
    activate.add_argument("document_id")
    return parser


async def _ingest(settings: Settings, args: argparse.Namespace) -> int:
    started = time.perf_counter()
    if not args.pdf.is_file():
        raise InvalidSourceError(f"PDF not found: {args.pdf}")
    sha256 = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    log.info(
        "ingestion_started",
        pdf=str(args.pdf),
        sha256=sha256,
        chunker_version=settings.chunker_version,
        embed_model=settings.embed_model,
        dry_run=args.dry_run,
    )
    if not args.dry_run and (existing := await _existing(settings, sha256)) is not None:
        log.info("ingestion_skipped_existing", document_id=str(existing))
        if args.activate:
            await _activate(settings, existing)
        return EXIT_OK

    result = run_pipeline(
        args.pdf,
        settings=settings,
        expected=load_fixture(args.expected),
        count_tokens=TokenizerCounter(settings.embed_model),
    )
    _write_artifacts(args.out, result)
    if not result.validation.ok:
        report = result.validation
        log.error(
            "ingestion_validation_failed",
            missing=report.missing,
            duplicates=report.duplicates,
            unexpected=report.unexpected,
            over_limit=report.over_limit,
        )
        return EXIT_VALIDATION
    if args.dry_run:
        log.info("ingestion_completed", n_chunks=len(result.chunks), dry_run=True)
        return EXIT_OK
    version_date = args.version_date or result.edition_date  # checked before minutes of embedding
    if version_date is None:
        raise InvalidSourceError("Edition date not found in the PDF; pass --version-date")

    embedder = BgeM3Embedder(
        settings.embed_model,
        device=settings.model_device,
        batch_size=settings.embed_batch_size,
        max_length=settings.embed_max_length,
    )
    document_id = await _store(settings, args, sha256, version_date, result, embedder)
    log.info(
        "ingestion_completed",
        document_id=str(document_id),
        n_chunks=len(result.chunks),
        duration_ms=round((time.perf_counter() - started) * 1000),
        activated=args.activate,
    )
    return EXIT_OK


def _write_artifacts(out: Path, result: PipelineResult) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in result.chunks:
            handle.write(json.dumps(chunk.as_dict(), ensure_ascii=False) + "\n")
    report = json.dumps(result.report(), indent=2, ensure_ascii=False)
    (out / "ingestion_report.json").write_text(report + "\n", encoding="utf-8")


async def _store(
    settings: Settings,
    args: argparse.Namespace,
    sha256: str,
    version_date: date,
    result: PipelineResult,
    embedder: Embedder,
) -> uuid.UUID:
    if embedder.dimension != EMBEDDING_DIM:
        raise InvalidSourceError(
            f"{embedder.model_id} gives {embedder.dimension}-d vectors; the schema expects "
            f"{EMBEDDING_DIM}"
        )
    started = time.perf_counter()
    vectors = embedder.embed([chunk.embed_text for chunk in result.chunks])
    log.info(
        "ingestion_stage_completed",
        stage="embed",
        count=len(vectors),
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    rows = [
        {**chunk.as_dict(), "embedding": vector}
        for chunk, vector in zip(result.chunks, vectors, strict=True)
    ]
    document = NewDocument(
        title=DOCUMENT_TITLE,
        source_url=args.source_url,
        version_date=version_date,
        sha256=sha256,
        chunker_version=settings.chunker_version,
        embed_model=settings.embed_model,
    )
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            repo = CorpusRepository(session)
            document_id = await repo.insert_document_with_chunks(document, rows)
            if args.activate:
                await repo.activate(document_id)
    finally:
        await engine.dispose()
    return document_id


async def _existing(settings: Settings, sha256: str) -> uuid.UUID | None:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            document = await CorpusRepository(session).find_document(
                sha256=sha256,
                chunker_version=settings.chunker_version,
                embed_model=settings.embed_model,
            )
            return document.id if document else None
    finally:
        await engine.dispose()


async def _activate(settings: Settings, document_id: uuid.UUID) -> None:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            await CorpusRepository(session).activate(document_id)
    finally:
        await engine.dispose()
    log.info("document_activated", document_id=str(document_id))


if __name__ == "__main__":
    sys.exit(main())
