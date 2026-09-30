"""Liveness and readiness probes (HLD §11, spec: foundation §3.7)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from samvidhan.api.deps import DbProbe, get_app_settings, get_db_probe
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
) -> ReadyResponse:
    """Dependencies reachable. Model and active-document checks are added in Phase 1/3."""
    try:
        async with asyncio.timeout(settings.readiness_timeout_s):
            await db_probe()
    except Exception as exc:
        log.warning("readiness_check_failed", check="database", error_type=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable") from exc
    return ReadyResponse(checks={"database": "ok"})
