"""FastAPI app factory. Run with `uvicorn --factory samvidhan.api.main:create_app`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from samvidhan import __version__
from samvidhan.api.errors import REQUEST_ID_HEADER, register_error_handlers
from samvidhan.api.middleware import RequestContextMiddleware
from samvidhan.api.routes import articles, chat, feedback, health, sessions
from samvidhan.api.services import AppServices, ServicesFactory, build_services
from samvidhan.core.config import Settings, get_settings
from samvidhan.core.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings
    factory: ServicesFactory = app.state.services_factory
    # The engine is lazy: the app starts with the DB down (/readyz reports it).
    services: AppServices = await factory(settings)
    app.state.services = services
    app.state.engine = services.engine
    log.info("app_started", log_level=settings.log_level, log_format=settings.log_format)
    try:
        yield
    finally:
        await services.aclose()
        log.info("app_stopped")


def create_app(
    settings: Settings | None = None, services_factory: ServicesFactory | None = None
) -> FastAPI:
    """`services_factory` defaults to the production wiring (loads local models); tests pass
    one that builds fakes."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="Samvidhan RAG", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.services_factory = services_factory or build_services

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
    for module in (sessions, chat, feedback, articles):
        app.include_router(module.router, prefix="/v1")
    return app
