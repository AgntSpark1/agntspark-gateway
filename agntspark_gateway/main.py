"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .error_handlers import register_error_handlers
from .routers import agents, api_keys, auth, health


def create_app() -> FastAPI:
    app = FastAPI(
        title="AgntSpark Gateway",
        description="API Gateway + Auth service for the AgntSpark AI Agent hosting platform",
        version="0.1.0",
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
