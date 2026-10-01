"""Concurrent answer cap per instance (`MAX_CONCURRENT_STREAMS`, HLD §13.2; spec:
api-sessions-memory §9.4). Non-blocking: a request that finds no free slot gets 503 `BUSY` instead
of queueing behind long answers."""

from typing import Any

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from samvidhan.core.errors import BusyError
from samvidhan.core.logging import get_logger

log = get_logger(__name__)


class Slot:
    """One taken slot; `release()` is idempotent so every exit path may call it."""

    def __init__(self, slots: "StreamSlots") -> None:
        self._slots: StreamSlots | None = slots

    def release(self) -> None:
        if self._slots is not None:
            self._slots._in_use -= 1
            self._slots = None


class StreamSlots:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._in_use = 0

    @property
    def in_use(self) -> int:
        return self._in_use

    def acquire(self) -> Slot:
        """Take a slot or raise `BusyError`. One event loop and no await, so no lock needed."""
        if self._in_use >= self._limit:
            log.warning(
                "limit_applied",
                limit="concurrent_streams",
                requested=self._in_use + 1,
                allowed=self._limit,
            )
            raise BusyError()
        self._in_use += 1
        return Slot(self)


class SlotStreamingResponse(StreamingResponse):
    """Frees the slot when the response is done, however it ends. Starlette skips both the body
    generator's `finally` and `background` when the client is gone before the first send."""

    def __init__(self, content: Any, slot: Slot, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._slot = slot

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._slot.release()
