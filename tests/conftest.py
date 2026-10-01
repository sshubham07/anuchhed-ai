"""Shared fixtures. Tests are marked `unit` / `integration` by directory."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from samvidhan.core.config import Settings

_TESTS = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        parts = Path(str(item.path)).relative_to(_TESTS).parts
        if parts and parts[0] in {"unit", "integration", "e2e"}:
            item.add_marker(getattr(pytest.mark, parts[0]))


@pytest.fixture(autouse=True)
def _database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DATABASE_URL` is required and has no default; give tests a dummy one."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:1/test")


@pytest.fixture
def settings() -> Settings:
    """Settings isolated from the developer's `.env`. The static UI mount is off so routes that
    tests add after `create_app` aren't shadowed by `/`; UI tests turn it on."""
    return Settings(_env_file=None, log_format="json", log_level="DEBUG", serve_ui=False)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Detach the root handler after each test: `configure_logging` binds it to the stream current
    at call time, and later log calls must not write to a closed capture stream."""
    yield
    logging.getLogger().handlers.clear()
    structlog.contextvars.clear_contextvars()
