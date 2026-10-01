"""Daily session expiry + feedback anonymization (HLD §9.1, spec: api-sessions-memory §9.1).

    python -m samvidhan.ops.cleanup [--dry-run] [--ttl-days N] [--batch-size 500]

Deletes sessions inactive for more than `SESSION_TTL_DAYS` (messages cascade); their feedback is
kept with its question/answer snapshot and no link back. Exit codes: 0 ok, 1 database error.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from samvidhan.core.config import get_settings
from samvidhan.core.logging import configure_logging, get_logger
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.db.repositories.chat import ExpiryCounts, ExpiryRepository

EXIT_OK, EXIT_DB_ERROR = 0, 1

log = get_logger(__name__)


async def expire_sessions(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ttl_days: int,
    batch_size: int,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ExpiryCounts:
    """Expire in batches, one transaction each, so a large backlog never holds long locks."""
    started = time.perf_counter()
    cutoff = (now or datetime.now(UTC)) - timedelta(days=ttl_days)
    total = ExpiryCounts(0, 0, 0)
    while True:
        async with session_factory() as db, db.begin():
            repo = ExpiryRepository(db)
            batch = await (repo.count(cutoff) if dry_run else repo.expire_batch(cutoff, batch_size))
        total = ExpiryCounts(
            total.sessions + batch.sessions,
            total.messages + batch.messages,
            total.feedback + batch.feedback,
        )
        if dry_run or batch.sessions < batch_size:
            break
    log.info(
        "sessions_expired",
        n_sessions=total.sessions,
        n_messages=total.messages,
        n_feedback_anonymized=total.feedback,
        ttl_days=ttl_days,
        dry_run=dry_run,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    return total


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="samvidhan.ops.cleanup")
    parser.add_argument("--ttl-days", type=int, default=settings.session_ttl_days)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="count, delete nothing")
    args = parser.parse_args(argv)
    if args.ttl_days < 1 or args.batch_size < 1:
        parser.error("--ttl-days and --batch-size must be >= 1")
    configure_logging(settings)

    async def run() -> ExpiryCounts:
        engine = create_engine(settings)
        try:
            return await expire_sessions(
                create_session_factory(engine),
                ttl_days=args.ttl_days,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
            )
        finally:
            await engine.dispose()

    try:
        counts = asyncio.run(run())
    except SQLAlchemyError:
        log.error("cleanup_database_error", exc_info=True)
        return EXIT_DB_ERROR
    verb = "would expire" if args.dry_run else "expired"
    sys.stdout.write(
        f"{verb} {counts.sessions} sessions ({counts.messages} messages); "
        f"{counts.feedback} feedback rows kept anonymized\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
