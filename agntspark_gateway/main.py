"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import runtime as rt
from .config import production_config_problems, settings
from .db import AsyncSessionLocal
from .error_handlers import register_error_handlers
from .observability import RequestContextMiddleware, configure_logging
from .routers import account, admin, agents, api_keys, auth, billing, health, ingress
from .scheduler import run_scheduler_loop
from .services import metering_service

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.agent_runtime = rt.build_core_runtime()
    await rt.startup_reconcile(app.state.agent_runtime)

    scheduler_task = asyncio.create_task(run_scheduler_loop(app.state.agent_runtime))
    try:
        yield
    finally:
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
        # Don't lose the requests counted since the last tick to a restart.
        try:
            async with AsyncSessionLocal() as db:
                await metering_service.flush_requests(db)
        except Exception as exc:
            log.warning("shutdown: flushing request counts failed", error=str(exc))


def init_error_reporting() -> bool:
    """Send unhandled errors to Sentry when a DSN is configured."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Request bodies hold passwords, tokens and agent secrets.
        send_default_pii=False,
        max_request_body_size="never",
    )
    return True


def create_app() -> FastAPI:
    problems = production_config_problems(settings)
    if problems:
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    configure_logging(settings)
    init_error_reporting()

    app = FastAPI(
        title="AgntSpark Gateway",
        description="API Gateway + Auth service for the AgntSpark AI Agent hosting platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Outermost, so the request id covers CORS and error responses too.
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(api_keys.router)
    app.include_router(admin.router)
    app.include_router(account.router)
    app.include_router(billing.router)
    app.include_router(agents.router)
    app.include_router(ingress.router)

    return app


app = create_app()
