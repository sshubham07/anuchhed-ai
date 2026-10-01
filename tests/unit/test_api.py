import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from samvidhan.api.deps import DbProbe, client_ip, get_corpus_probe, get_db_probe
from samvidhan.api.main import create_app
from samvidhan.core.config import Settings
from samvidhan.core.errors import NotFoundError
from samvidhan.core.logging import configure_logging, get_logger
from tests.api_helpers import fake_services


class Echo(BaseModel):
    n: int


async def _ok_probe() -> None:
    return None


async def _failing_probe() -> None:
    raise ConnectionRefusedError("db down")


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("Article 999 not found")

    @app.post("/echo")
    async def echo(body: Echo) -> Echo:
        return body

    @app.get("/logs")
    async def logs() -> dict[str, str]:
        get_logger("ours").info("router_completed")
        logging.getLogger("sqlalchemy.pool").warning("pool event")
        return {"ok": "yes"}


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings, fake_services())
    _add_test_routes(app)
    app.dependency_overrides[get_db_probe] = lambda: _ok_probe
    app.dependency_overrides[get_corpus_probe] = lambda: _ok_probe
    return app


@pytest.fixture
def log_stream(settings: Settings, app: FastAPI) -> io.StringIO:
    out = io.StringIO()
    configure_logging(settings, stream=out)
    return out


@pytest.fixture
def client(app: FastAPI, log_stream: io.StringIO) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_readyz_ok(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "active_document": "ok", "models": "ok"},
    }


def test_readyz_db_down_returns_503_envelope(
    app: FastAPI, client: TestClient, log_stream: io.StringIO
) -> None:
    probe: DbProbe = _failing_probe
    app.dependency_overrides[get_db_probe] = lambda: probe
    response = client.get("/readyz", headers={"X-Request-ID": "ready-1"})
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "SERVICE_UNAVAILABLE",
            "message": "Database unavailable",
            "request_id": "ready-1",
        }
    }
    events = {line["event"]: line for line in _lines(log_stream)}
    assert events["readiness_check_failed"]["request_id"] == "ready-1"


def test_valid_incoming_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123.x_Y"})
    assert response.headers["X-Request-ID"] == "abc-123.x_Y"


@pytest.mark.parametrize("bad", ["has space", "x" * 129, "a;b=<c>", ""])
def test_invalid_incoming_request_id_is_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": bad})
    assert response.headers["X-Request-ID"] != bad
    assert len(response.headers["X-Request-ID"]) == 32


def test_every_line_in_a_request_shares_the_request_id(
    client: TestClient, log_stream: io.StringIO
) -> None:
    client.get("/logs", headers={"X-Request-ID": "req-42"})
    lines = _lines(log_stream)
    by_event = {line["event"]: line for line in lines}
    assert {"router_completed", "pool event", "http_request_completed"} <= by_event.keys()
    for event in ("router_completed", "pool event", "http_request_completed"):
        assert by_event[event]["request_id"] == "req-42"
    completed = by_event["http_request_completed"]
    assert completed["status_code"] == 200
    assert completed["path"] == "/logs"
    assert completed["level"] == "info"
    assert completed["duration_ms"] >= 0


def test_request_ids_do_not_leak_between_requests(
    client: TestClient, log_stream: io.StringIO
) -> None:
    client.get("/logs", headers={"X-Request-ID": "first"})
    client.get("/logs")
    ids = {line["request_id"] for line in _lines(log_stream) if line["event"] == "router_completed"}
    assert len(ids) == 2
    assert "first" in ids


def test_probe_requests_log_at_debug(client: TestClient, log_stream: io.StringIO) -> None:
    client.get("/healthz")
    lines = _lines(log_stream)
    [completed] = [line for line in lines if line["event"] == "http_request_completed"]
    assert completed["level"] == "debug"


def test_unhandled_exception_returns_generic_500_envelope(
    client: TestClient, log_stream: io.StringIO
) -> None:
    response = client.get("/boom", headers={"X-Request-ID": "boom-1"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "boom-1"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Internal server error",
            "request_id": "boom-1",
        }
    }
    assert "secret internal detail" not in response.text
    failed = [line for line in _lines(log_stream) if line["event"] == "request_failed"]
    assert failed[0]["request_id"] == "boom-1"
    assert "RuntimeError" in failed[0]["exception"]


def test_domain_error_envelope(client: TestClient) -> None:
    response = client.get("/missing", headers={"X-Request-ID": "m-1"})
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Article 999 not found",
        "request_id": "m-1",
    }


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_validation_error_envelope(client: TestClient) -> None:
    response = client.post("/echo", json={"n": "not-a-number"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"].startswith("body.n:")


def test_cors_preflight_allows_ui_origin(client: TestClient) -> None:
    response = client.options(
        "/healthz",
        headers={"Origin": "http://localhost:8501", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8501"
    assert response.headers["X-Request-ID"]


# ---- /v1/chat input checks and rate limits (no DB: they run before the session lookup) ----

SESSION = "0190f3c4-0000-7000-8000-000000000001"


@pytest.mark.parametrize(
    ("message", "code"),
    [("   ", "EMPTY_MESSAGE"), ("x" * 4001, "MESSAGE_TOO_LONG")],
)
def test_chat_rejects_bad_messages_before_any_llm_call(
    client: TestClient, log_stream: io.StringIO, message: str, code: str
) -> None:
    response = client.post("/v1/chat", json={"session_id": SESSION, "message": message})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    rejected = [line for line in _lines(log_stream) if line["event"] == "input_rejected"]
    assert rejected and rejected[0]["reason"] in {"empty", "too_long"}
    if code == "MESSAGE_TOO_LONG":
        assert "4000" in response.json()["error"]["message"]


def test_chat_rate_limit_per_session_returns_429_with_retry_after(settings: Settings) -> None:
    app = create_app(
        settings.model_copy(update={"rate_limit_session": "2/minute"}), fake_services()
    )
    log_stream = io.StringIO()
    configure_logging(settings, stream=log_stream)  # create_app configured stdout
    with TestClient(app, raise_server_exceptions=False) as client:
        codes = [
            client.post("/v1/chat", json={"session_id": SESSION, "message": " "}).status_code
            for _ in range(3)
        ]
        other = client.post(
            "/v1/chat",
            json={"session_id": "0190f3c4-0000-7000-8000-000000000002", "message": " "},
        )
        limited = client.post("/v1/chat", json={"session_id": SESSION, "message": " "})
    assert codes == [422, 422, 429]
    assert other.status_code == 422  # a different session has its own budget
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert any(
        line["event"] == "rate_limited" and line["scope"] == "session"
        for line in _lines(log_stream)
    )


def test_chat_rate_limit_per_ip(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"rate_limit_ip": "1/minute"}), fake_services())
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/v1/chat", json={"session_id": SESSION, "message": " "})
        second = client.post(
            "/v1/chat",
            json={"session_id": "0190f3c4-0000-7000-8000-000000000002", "message": " "},
        )
    assert (first.status_code, second.status_code) == (422, 429)


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("10.0.0.5", "203.0.113.7, 10.0.0.5", "203.0.113.7"),  # trusted UI server forwards
        ("198.51.100.9", "203.0.113.7", "198.51.100.9"),  # untrusted peer can't spoof
        ("10.0.0.5", None, "10.0.0.5"),
    ],
)
def test_client_ip_trusts_forwarded_for_only_from_trusted_proxies(
    peer: str, forwarded: str | None, expected: str
) -> None:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    request = Request({"type": "http", "client": (peer, 1234), "headers": headers})
    assert client_ip(request, ["10.0.0.5"]) == expected


def test_chat_rejects_unknown_answer_style(client: TestClient) -> None:
    response = client.post(
        "/v1/chat", json={"session_id": SESSION, "message": "hi", "answer_style": "poem"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---- Static web UI (ADR-0013) ----


def _ui_client(settings: Settings, tmp_path: Any, **overrides: Any) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html><title>Samvidhan</title>")
    update = {"serve_ui": True, "ui_dir": tmp_path, **overrides}
    app = create_app(settings.model_copy(update=update), fake_services())
    return TestClient(app, raise_server_exceptions=False)


def test_ui_is_served_at_root_without_shadowing_the_api(settings: Settings, tmp_path: Any) -> None:
    with _ui_client(settings, tmp_path) as client:
        page = client.get("/")
        assert page.status_code == 200 and "Samvidhan" in page.text
        assert page.headers["cache-control"] == "no-cache"  # never serve a stale UI
        assert client.get("/healthz").json() == {"status": "ok"}
        missing = client.get("/v1/nope")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_ui_mount_can_be_disabled(settings: Settings, tmp_path: Any) -> None:
    with _ui_client(settings, tmp_path, serve_ui=False) as client:
        assert client.get("/").status_code == 404


def test_missing_ui_dir_does_not_break_startup(settings: Settings, tmp_path: Any) -> None:
    update = {"serve_ui": True, "ui_dir": tmp_path / "nope"}
    app = create_app(settings.model_copy(update=update), fake_services())
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
