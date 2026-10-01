"""FastAPI dependencies. Heavy objects live on `app.state` (built in the lifespan)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from samvidhan.api.services import AppServices
from samvidhan.core.config import Settings
from samvidhan.core.errors import ServiceUnavailableError
from samvidhan.core.ids import hash_ip
from samvidhan.db.engine import ping
from samvidhan.db.repositories.corpus import CorpusRepository

DbProbe = Callable[[], Awaitable[None]]


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def get_services(request: Request) -> AppServices:
    return cast(AppServices, request.app.state.services)


def get_db_probe(request: Request) -> DbProbe:
    return partial(ping, get_engine(request))


def get_corpus_probe(request: Request) -> DbProbe:
    """Raises unless an active document is present (readiness)."""
    services = get_services(request)

    async def probe() -> None:
        async with services.session_factory() as session:
            if await CorpusRepository(session).active_version_date() is None:
                raise ServiceUnavailableError("No active document; run the ingestion first")

    return probe


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One DB session per request; routes commit explicitly."""
    async with get_services(request).session_factory() as session:
        yield session


def client_ip(request: Request, trusted_proxies: list[str]) -> str:
    """The end user's IP. `X-Forwarded-For` is honoured only from a trusted proxy (the UI server
    calls the API for every user, so its own address would make per-IP limits global)."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in trusted_proxies:
        return forwarded.split(",")[0].strip() or peer
    return peer


def get_client_ip_hash(request: Request) -> str:
    settings = get_app_settings(request)
    ip = client_ip(request, settings.trusted_proxy_ips)
    return hash_ip(ip, settings.ip_hash_salt.get_secret_value())
