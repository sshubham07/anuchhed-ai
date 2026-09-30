"""FastAPI app factory. Run with `uvicorn --factory samvidhan.api.main:create_app`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from samvidhan import __version__
from samvidhan.api.errors import REQUEST_ID_HEADER, register_error_handlers
from samvidhan.api.middleware import RequestContextMiddleware
from samvidhan.api.routes import health
from samvidhan.core.config import Settings, get_settings
from samvidhan.core.logging import configure_logging, get_logger
from samvidhan.db.engine import create_engine

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings
    # Lazy: no connection is opened here, so the app starts with the DB down (/readyz reports it).
    app.state.engine = create_engine(settings)
    log.info("app_started", log_level=settings.log_level, log_format=settings.log_format)
    try:
        yield
    finally:
        await app.state.engine.dispose()
        log.info("app_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="Samvidhan RAG", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last = outermost, so CORS and error responses also get a request id.
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app
