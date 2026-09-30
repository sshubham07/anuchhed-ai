"""Request context middleware (spec: foundation §3.5).

Pure ASGI (not BaseHTTPMiddleware) so it also wraps streaming responses. Binds `request_id` into
structlog contextvars, echoes `X-Request-ID`, logs one `http_request_completed` line, and turns
unhandled exceptions into the 500 error envelope.
"""

import logging
import time

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from samvidhan.api.errors import REQUEST_ID_HEADER, error_response
from samvidhan.core.ids import is_valid_request_id, new_request_id
from samvidhan.core.logging import get_logger

log = get_logger(__name__)

PROBE_PATHS = frozenset({"/healthz", "/readyz"})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and is_valid_request_id(incoming) else new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        # Each request runs in its own task context, so bindings don't leak between requests.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        response_started = False
        started_at = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            log.error("request_failed", error_code="INTERNAL_ERROR", exc_info=True)
            if response_started:
                raise
            response = error_response(500, "INTERNAL_ERROR", "Internal server error", request_id)
            await response(scope, receive, send_with_request_id)
        finally:
            path = scope.get("path", "")
            log.log(
                logging.DEBUG if path in PROBE_PATHS else logging.INFO,
                "http_request_completed",
                method=scope.get("method"),
                path=path,
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
            )
