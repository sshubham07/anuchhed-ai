"""Liveness and readiness probes (HLD §11, spec: foundation §3.7)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from samvidhan.api.deps import DbProbe, get_app_settings, get_corpus_probe, get_db_probe
from samvidhan.api.schemas import ErrorEnvelope, HealthResponse, ReadyResponse
from samvidhan.core.config import Settings
from samvidhan.core.errors import ServiceUnavailableError
from samvidhan.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> HealthResponse:
    """Process is alive. No I/O."""
    return HealthResponse()


@router.get("/readyz", responses={503: {"model": ErrorEnvelope}})
async def readyz(
    settings: Annotated[Settings, Depends(get_app_settings)],
    db_probe: Annotated[DbProbe, Depends(get_db_probe)],
    corpus_probe: Annotated[DbProbe, Depends(get_corpus_probe)],
) -> ReadyResponse:
    """Database reachable and an active document present. Models are loaded before the app
    starts serving (lifespan), so a running app has them."""
    for check, probe, message in (
        ("database", db_probe, "Database unavailable"),
        ("active_document", corpus_probe, "No active document"),
    ):
        try:
            async with asyncio.timeout(settings.readiness_timeout_s):
                await probe()
        except Exception as exc:
            log.warning("readiness_check_failed", check=check, error_type=type(exc).__name__)
            raise ServiceUnavailableError(message) from exc
    # The lifespan fails when the models can't load, so a serving app has them.
    return ReadyResponse(checks={"database": "ok", "active_document": "ok", "models": "ok"})
