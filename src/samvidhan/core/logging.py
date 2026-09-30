"""Structured logging (spec: foundation §3.5, observability §1).

structlog renders our events; std-lib logging (uvicorn, sqlalchemy, asyncpg, httpx, litellm,
alembic) is routed through the same processor chain, so every line has the same shape and carries
the `request_id` bound in contextvars by the request middleware.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, TextIO

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from samvidhan.core.config import Settings

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(r"api_key|password|secret|token|authorization|database_url", re.IGNORECASE)

# Libraries that are chatty at INFO. Pinned to WARNING regardless of LOG_LEVEL (observability §1.1).
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "sqlalchemy.engine",
    "asyncpg",
    "litellm",
    "LiteLLM",
    "uvicorn.access",
)
# uvicorn installs its own handlers with propagate=False; hand them back to the root handler.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _SECRET_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    return value


def redact_secrets(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Replace values of secret-looking keys. Defence in depth: callers still must not log them."""
    for key in list(event_dict):
        if _SECRET_KEY.search(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact(event_dict[key])
    return event_dict


def _static_fields(settings: Settings) -> Processor:
    fields = {
        "service": settings.service_name,
        "env": settings.env,
        "version": settings.app_version,
    }

    def add_static_fields(
        _: WrappedLogger, __: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        for key, value in fields.items():
            event_dict.setdefault(key, value)
        return event_dict

    return add_static_fields


def _shared_processors(settings: Settings) -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _static_fields(settings),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        redact_secrets,
    ]


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    """Configure structlog + std-lib logging. Idempotent; call once at each entry point."""
    shared = _shared_processors(settings)
    renderer: Processor
    final: list[Processor]
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
        final = [structlog.processors.format_exc_info, renderer]
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=(stream or sys.stdout).isatty())
        final = [renderer]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *final],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
