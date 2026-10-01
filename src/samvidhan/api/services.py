"""Long-lived objects the API needs, built once in the lifespan (spec: api-sessions-memory §3.7).

`create_app(settings, services_factory=...)` lets tests swap in fakes (FakeLLM, fake retrieval) so
nothing loads local models or calls a provider.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from samvidhan.api.concurrency import StreamSlots
from samvidhan.api.ratelimit import RateLimiter
from samvidhan.core.config import Settings
from samvidhan.core.logging import get_logger
from samvidhan.db.engine import create_engine, create_session_factory
from samvidhan.db.repositories.corpus import CorpusRepository
from samvidhan.graph.builder import ChatGraph, build_graph
from samvidhan.graph.nodes import GraphDeps, load_prompts
from samvidhan.llm.client import LLMClient
from samvidhan.memory.store import PostgresSessionStore
from samvidhan.retrieval.service import RetrievalService

log = get_logger(__name__)


@dataclass(slots=True)
class AppServices:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    llm: LLMClient
    graph: ChatGraph
    limiter: RateLimiter
    streams: StreamSlots

    async def aclose(self) -> None:
        await self.llm.recorder.drain()  # pending `llm_calls` rows, before the pool closes
        await self.engine.dispose()


ServicesFactory = Callable[[Settings], Awaitable[AppServices]]


async def assemble_services(
    settings: Settings,
    engine: AsyncEngine,
    llm: LLMClient,
    retrieval: RetrievalService,
) -> AppServices:
    """Wire the graph over Postgres memory. The DB may be down at startup: the edition shown in
    the disclaimer is then left out and `/readyz` reports the outage."""
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            edition = await CorpusRepository(session).active_version_date()
    except Exception as exc:
        log.warning("edition_unavailable", error_type=type(exc).__name__)
        edition = None
    deps = GraphDeps(
        llm=llm,
        retrieval=retrieval,
        memory=PostgresSessionStore(
            session_factory, history_messages=settings.router_history_messages
        ),
        settings=settings,
        prompts=load_prompts(settings),
        edition=edition,
    )
    return AppServices(
        engine=engine,
        session_factory=session_factory,
        llm=llm,
        graph=build_graph(deps),
        limiter=RateLimiter(settings),
        streams=StreamSlots(settings.max_concurrent_streams),
    )


async def build_services(settings: Settings) -> AppServices:
    """Production wiring: LiteLLM client with `llm_calls` logging, local embedder + reranker."""
    from samvidhan.llm.factory import create_llm_client
    from samvidhan.retrieval.service import create_retrieval_service

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    # Loading the models takes seconds and is CPU-bound: keep the event loop free meanwhile.
    retrieval = await asyncio.to_thread(create_retrieval_service, settings, session_factory)
    llm = create_llm_client(settings, session_factory)
    return await assemble_services(settings, engine, llm, retrieval)
