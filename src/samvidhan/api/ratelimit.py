"""Per-IP and per-session request limits (HLD §8.4, spec: api-sessions-memory §3.6).

Uses the `limits` library directly (moving window, in-process storage): the per-session key is in
the JSON body, which slowapi's key function cannot read. One instance only; a Redis storage would
be needed for more than one replica.
"""

import math
import time
import uuid

from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import MovingWindowRateLimiter

from samvidhan.core.config import Settings
from samvidhan.core.errors import RateLimitedError
from samvidhan.core.logging import get_logger

log = get_logger(__name__)


class RateLimiter:
    def __init__(self, settings: Settings) -> None:
        self._limiter = MovingWindowRateLimiter(MemoryStorage())
        self._per_ip: list[RateLimitItem] = [
            parse(settings.rate_limit_ip),
            parse(settings.rate_limit_ip_daily),
        ]
        self._per_session: list[RateLimitItem] = [parse(settings.rate_limit_session)]

    async def check_ip(self, ip_hash: str) -> None:
        """Count one request against the per-IP limits only (feedback)."""
        await self._hit("ip", ip_hash, self._per_ip)

    async def check_chat(self, ip_hash: str, session_id: uuid.UUID) -> None:
        """Count one chat request; raise `RateLimitedError` when any limit is exceeded."""
        await self._hit("ip", ip_hash, self._per_ip)
        await self._hit("session", str(session_id), self._per_session)

    async def _hit(self, scope: str, key: str, items: list[RateLimitItem]) -> None:
        for item in items:
            if not await self._limiter.hit(item, scope, key):
                stats = await self._limiter.get_window_stats(item, scope, key)
                retry_after = max(1, math.ceil(stats.reset_time - time.time()))
                log.info("rate_limited", scope=scope, limit=str(item))
                raise RateLimitedError(retry_after)
