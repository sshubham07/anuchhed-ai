import io
import json
import logging
from typing import Any

import pytest
import structlog

from samvidhan.core.config import Settings
from samvidhan.core.logging import REDACTED, configure_logging, get_logger


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.fixture
def stream(settings: Settings) -> io.StringIO:
    out = io.StringIO()
    configure_logging(settings, stream=out)
    structlog.contextvars.clear_contextvars()
    return out


def test_json_line_has_standard_fields(stream: io.StringIO) -> None:
    get_logger("test").info("retrieval_completed", duration_ms=12.5)
    [line] = _lines(stream)
    assert line["event"] == "retrieval_completed"
    assert line["level"] == "info"
    assert line["service"] == "samvidhan-api"
    assert line["env"] == "dev"
    assert line["version"] == "dev"
    assert line["timestamp"].endswith("Z")
    assert line["duration_ms"] == 12.5


def test_contextvars_reach_structlog_and_stdlib_lines(stream: io.StringIO) -> None:
    structlog.contextvars.bind_contextvars(request_id="req-1", session_id="sess-1")
    get_logger("ours").info("router_completed")
    logging.getLogger("some.library").warning("library says hi")
    ours, library = _lines(stream)
    for line in (ours, library):
        assert line["request_id"] == "req-1"
        assert line["session_id"] == "sess-1"
        assert line["service"] == "samvidhan-api"
    assert library["event"] == "library says hi"
    assert library["level"] == "warning"


def test_secret_keys_are_redacted(stream: io.StringIO) -> None:
    get_logger().info(
        "config_loaded",
        groq_api_key="gsk_x",
        DATABASE_URL="postgresql://u:p@h/db",
        nested={"password": "p", "ok": 1},
    )
    [line] = _lines(stream)
    assert line["groq_api_key"] == REDACTED
    assert line["DATABASE_URL"] == REDACTED
    assert line["nested"] == {"password": REDACTED, "ok": 1}


def test_exceptions_are_rendered(stream: io.StringIO) -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        get_logger().error("request_failed", exc_info=True)
    [line] = _lines(stream)
    assert "RuntimeError: boom" in line["exception"]


def test_noisy_libraries_pinned_to_warning(stream: io.StringIO) -> None:
    logging.getLogger("sqlalchemy.engine").info("SELECT 1")
    logging.getLogger("httpx").info("GET /")
    assert _lines(stream) == []


def test_level_filtering(settings: Settings) -> None:
    out = io.StringIO()
    configure_logging(settings.model_copy(update={"log_level": "WARNING"}), stream=out)
    get_logger().info("dropped")
    get_logger().warning("kept")
    assert [line["event"] for line in _lines(out)] == ["kept"]


def test_console_format_renders_plain_text(settings: Settings) -> None:
    out = io.StringIO()
    configure_logging(settings.model_copy(update={"log_format": "console"}), stream=out)
    get_logger().info("app_started")
    assert "app_started" in out.getvalue()
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.getvalue())
