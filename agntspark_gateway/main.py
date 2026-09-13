"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import runtime as rt
from .config import settings
from .error_handlers import register_error_handlers
from .routers import agents, api_keys, auth, health
from .scheduler import run_scheduler_loop

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


def create_app() -> FastAPI:
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

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(api_keys.router)
    app.include_router(agents.router)

    return app


app = create_app()
