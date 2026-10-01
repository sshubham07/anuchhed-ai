"""Error envelope and exception handlers (standards §3–4, spec: foundation §3.6).

Unhandled exceptions are caught by `RequestContextMiddleware`, which uses `error_response` too, so
every error has the same shape and carries `X-Request-ID`.
"""

from http import HTTPStatus
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from samvidhan.api.schemas import ErrorBody, ErrorEnvelope
from samvidhan.core.errors import RateLimitedError, SamvidhanError, ValidationFailedError

REQUEST_ID_HEADER = "X-Request-ID"

_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 429: "RATE_LIMITED"}


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id))
    all_headers = {**(headers or {}), **({REQUEST_ID_HEADER: request_id} if request_id else {})}
    return JSONResponse(envelope.model_dump(), status_code=status_code, headers=all_headers)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


async def _handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(SamvidhanError, exc)
    # The raise site logs with context; the middleware logs the status. No duplicate line here.
    headers = {"Retry-After": str(exc.retry_after_s)} if isinstance(exc, RateLimitedError) else None
    return error_response(exc.http_status, exc.code, exc.message, _request_id(request), headers)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = f"{location}: {first.get('msg', 'invalid')}" if location else "Invalid request"
    return error_response(422, ValidationFailedError.code, message, _request_id(request))


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(exc.status_code, code, message, _request_id(request))


# Handlers take `Exception` to match Starlette's handler type; each is registered for one class.
def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(SamvidhanError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
