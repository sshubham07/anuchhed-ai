import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from samvidhan.api.deps import DbProbe, get_db_probe
from samvidhan.api.main import create_app
from samvidhan.core.config import Settings
from samvidhan.core.errors import NotFoundError
from samvidhan.core.logging import configure_logging, get_logger


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
    app = create_app(settings)
    _add_test_routes(app)
    app.dependency_overrides[get_db_probe] = lambda: _ok_probe
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
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


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
