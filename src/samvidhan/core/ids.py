"""Identifier helpers."""

import hashlib
import re
import uuid

from uuid_utils.compat import uuid7

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def is_valid_request_id(value: str) -> bool:
    """Accept only short, log-safe ids from clients (prevents log injection via `X-Request-ID`)."""
    return bool(_REQUEST_ID.fullmatch(value))


def new_session_id() -> uuid.UUID:
    """UUIDv7: time-ordered, so sessions sort by creation (HLD §9.1)."""
    return uuid7()


def hash_ip(ip: str, salt: str) -> str:
    """sha256(ip + salt): for rate limits and analytics; the raw IP is never stored or logged."""
    return hashlib.sha256(f"{ip}{salt}".encode()).hexdigest()
