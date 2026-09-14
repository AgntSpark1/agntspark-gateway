"""Global exception → HTTP response mapping.

Every ``AgntSparkError`` (core or gateway) is serialised via its own
``.to_dict()`` into the ``{"code", "message", "details"}`` envelope that
agntspark-sdk's ``Client._handle_error`` already parses (it reads
``body["message"]`` and branches on the HTTP status code).
"""

from __future__ import annotations

from agntspark_core.exceptions import AgntSparkError, AuthenticationError, AuthorisationError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .exceptions import (
    ApiKeyNotFoundError,
    BuildNotSupportedError,
    DeploymentFailedError,
    EmailAlreadyRegisteredError,
    InvalidInviteError,
    NotFoundError,
    NotImplementedStubError,
    RateLimitExceededError,
    RegistrationClosedError,
)

_STATUS_MAP: dict[type[AgntSparkError], int] = {
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    AuthorisationError: status.HTTP_403_FORBIDDEN,
    InvalidInviteError: status.HTTP_403_FORBIDDEN,
    RegistrationClosedError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ApiKeyNotFoundError: status.HTTP_404_NOT_FOUND,
    EmailAlreadyRegisteredError: status.HTTP_409_CONFLICT,
    RateLimitExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    NotImplementedStubError: status.HTTP_501_NOT_IMPLEMENTED,
    BuildNotSupportedError: status.HTTP_501_NOT_IMPLEMENTED,
    DeploymentFailedError: status.HTTP_502_BAD_GATEWAY,
}


def _status_for(exc: AgntSparkError) -> int:
    for exc_type, code in _STATUS_MAP.items():
        if isinstance(exc, exc_type):
            return code
    # An AgntSparkError with no explicit mapping is treated as an internal
    # failure (e.g. SecretDecryptionError) rather than a client error.
    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def agntspark_error_handler(request: Request, exc: AgntSparkError) -> JSONResponse:
    headers = {}
    retry_after = exc.details.get("retry_after") if exc.details else None
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=_status_for(exc), content=exc.to_dict(), headers=headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed.",
            "details": {"errors": exc.errors()},
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AgntSparkError, agntspark_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)


__all__ = ["register_error_handlers"]
