"""FastAPI dependencies. Heavy objects live on `app.state` (built in the lifespan)."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from samvidhan.core.config import Settings
from samvidhan.db.engine import ping

DbProbe = Callable[[], Awaitable[None]]


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def get_db_probe(request: Request) -> DbProbe:
    return partial(ping, get_engine(request))
