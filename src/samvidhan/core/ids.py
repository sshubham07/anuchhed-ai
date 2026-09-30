"""Identifier helpers."""

import re
import uuid

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def is_valid_request_id(value: str) -> bool:
    """Accept only short, log-safe ids from clients (prevents log injection via `X-Request-ID`)."""
    return bool(_REQUEST_ID.fullmatch(value))
