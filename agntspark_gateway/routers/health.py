"""Liveness / readiness probes, and the Prometheus scrape endpoint."""

from __future__ import annotations

import structlog
from agntspark_core import metrics as core_metrics
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
async def readyz(db: AsyncSession = Depends(get_db)) -> dict[str, str] | JSONResponse:
    """Ready = can reach the database. Uptime checks and the deploy smoke test
    use this rather than /healthz, which only shows the process is up."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("readyz: database unreachable", error=str(exc))
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ready"}


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition format.

    Exposes agntspark_core's metrics registry — the same Counters/Gauges/
    Histograms that AgentRuntime already updates on every deploy/scale/
    health-check call (agntspark_containers_active, _container_cpu_percent,
    _container_memory_mb, _agents_total, ...). This is live data reflecting
    what the gateway's own runtime is actually doing, not a separate/fake
    metrics source.
    """
    return Response(content=core_metrics.export(), media_type=CONTENT_TYPE_LATEST)
