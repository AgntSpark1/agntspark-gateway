"""Logging, request IDs and access logs.

In production every log line is one JSON object (what log shippers and
``docker logs | jq`` expect); elsewhere structlog's readable console output.
Every request gets an id (the caller's ``X-Request-ID`` when it's sane,
otherwise a fresh one), bound to all log lines written while handling it and
returned in the ``X-Request-ID`` response header, so a user's error report can
be matched to the server's logs.
"""

from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import GatewaySettings

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Probes and scrapes would drown out real traffic.
_QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})

access_log = structlog.get_logger("agntspark_gateway.access")


def configure_logging(settings: GatewaySettings) -> None:
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    production = settings.environment == "production"
    renderer: Any = (
        structlog.processors.JSONRenderer() if production else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info if production else _passthrough,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(levelname)s %(name)s %(message)s")


def _passthrough(logger: Any, method: str, event: dict[str, Any]) -> dict[str, Any]:
    # ConsoleRenderer pretty-prints exc_info itself.
    return event


def request_id_for(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            if _VALID_REQUEST_ID.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Assigns the request id, echoes it back, and writes one access log line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = request_id_for(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status_code = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k != REQUEST_ID_HEADER.encode()
                ]
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            if path not in _QUIET_PATHS:
                access_log.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )


__all__ = ["RequestContextMiddleware", "configure_logging", "request_id_for"]
