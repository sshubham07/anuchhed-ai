"""Daily per-model budget guard (spec: llm-router-generation §3.4, HLD §8.6).

Usage is counted from `llm_calls` (every status: providers count failed requests too), cached per
model for `cache_s` and incremented locally after each call. Fails open on a counting error.
"""

import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from samvidhan.core.logging import get_logger
from samvidhan.db.repositories.llm_calls import LlmCallRepository

log = get_logger(__name__)

UsageCounter = Callable[[str], Awaitable[int]]


def db_usage_counter(session_factory: async_sessionmaker[AsyncSession]) -> UsageCounter:
    """Rows for a model since 00:00 UTC today."""

    async def count(model: str) -> int:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with session_factory() as session:
            return await LlmCallRepository(session).count_since(model, midnight)

    return count


class BudgetGuard:
    def __init__(
        self,
        counter: UsageCounter,
        caps: Mapping[str, int],
        warn_ratio: float,
        *,
        cache_s: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._counter = counter
        self._caps = dict(caps)
        self._warn_ratio = warn_ratio
        self._cache_s = cache_s
        self._clock = clock
        self._cache: dict[str, tuple[float, int]] = {}  # model → (fetched_at, count)

    async def allows(self, model: str) -> bool:
        """False when `model` is at or above `cap × warn_ratio` today (logs `budget_near_cap`)."""
        cap = self._caps.get(model)
        if not cap:
            return True
        try:
            used = await self._used(model)
        except Exception as exc:  # fail open: a counting error must not block answers
            log.warning("budget_check_failed", model=model, error=repr(exc))
            return True
        if used >= cap * self._warn_ratio:
            log.warning("budget_near_cap", model=model, used_today=used, cap=cap)
            return False
        return True

    def note_call(self, model: str) -> None:
        """Count a call made since the last fetch, so the cache doesn't lag behind."""
        if model in self._cache:
            fetched_at, count = self._cache[model]
            self._cache[model] = (fetched_at, count + 1)

    async def _used(self, model: str) -> int:
        now = self._clock()
        cached = self._cache.get(model)
        if cached is not None and now - cached[0] < self._cache_s:
            return cached[1]
        count = await self._counter(model)
        self._cache[model] = (now, count)
        return count
