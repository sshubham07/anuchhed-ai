"""Where `llm_calls` rows go (observability §1.5).

`DbCallRecorder` writes in a background task so the request path never waits on the insert;
inserts run one at a time in call order (row ids follow attempt order; telemetry holds at most
one pool connection). `drain()` awaits pending writes (CLIs before exit, tests counting rows).
"""

import asyncio
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from samvidhan.core.logging import get_logger
from samvidhan.db.repositories.llm_calls import LlmCallRepository
from samvidhan.llm.types import CallRecord

log = get_logger(__name__)


class CallRecorder(Protocol):
    def record(self, record: CallRecord) -> None: ...

    async def drain(self) -> None: ...


class MemoryCallRecorder:
    """Keeps rows in a list (unit tests, `--fake-llm` CLI runs without a DB write)."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def record(self, record: CallRecord) -> None:
        self.records.append(record)

    async def drain(self) -> None:
        return None


class DbCallRecorder:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._pending: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()  # FIFO: tasks acquire it in creation order

    def record(self, record: CallRecord) -> None:
        task = asyncio.get_running_loop().create_task(self._insert(record))
        self._pending.add(task)  # keep a reference until done (asyncio drops weak ones)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def _insert(self, record: CallRecord) -> None:
        try:
            async with self._lock, self._session_factory() as session, session.begin():
                await LlmCallRepository(session).insert(record)
        except Exception as exc:  # telemetry must never break a request
            log.error("llm_call_record_failed", error=repr(exc), exc_info=True)


class TeeRecorder:
    """Sends each row to several recorders (e.g. the DB and a CLI usage summary)."""

    def __init__(self, *recorders: CallRecorder) -> None:
        self._recorders = recorders

    def record(self, record: CallRecord) -> None:
        for recorder in self._recorders:
            recorder.record(record)

    async def drain(self) -> None:
        for recorder in self._recorders:
            await recorder.drain()
