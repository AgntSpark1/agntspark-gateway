"""Gateway-specific exceptions.

``AuthenticationError`` / ``AuthorisationError`` are imported straight from
``agntspark_core.exceptions`` so error codes stay unified across services.
Everything below is gateway-specific and subclasses the same
``AgntSparkError`` base so ``.to_dict()`` produces the ``{"code", "message",
"details"}`` envelope the SDK's ``Client._handle_error`` already parses.
"""

from __future__ import annotations

from typing import Any

from agntspark_core.exceptions import AgntSparkError, AuthenticationError, AuthorisationError


class EmailAlreadyRegisteredError(AgntSparkError):
    """Raised when registering with an email that already has an account."""

    def __init__(self, email: str) -> None:
        super().__init__(
            f"An account with email {email!r} already exists.",
            code="EMAIL_ALREADY_REGISTERED",
            details={"email": email},
        )


class NotFoundError(AgntSparkError):
    """Raised when a requested resource does not exist (or isn't owned by the caller)."""

    def __init__(self, resource: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{resource} not found.", code="NOT_FOUND", details=details)


class ApiKeyNotFoundError(NotFoundError):
    """Raised when an API key id does not exist or does not belong to the caller."""

    def __init__(self, key_id: str) -> None:
        super().__init__("API key", details={"key_id": key_id})


class NotImplementedStubError(AgntSparkError):
    """Raised by reserved-but-unbuilt routes (e.g. /v1/agents/*)."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"{path!r} is reserved by the API contract but not implemented yet.",
            code="NOT_IMPLEMENTED",
            details={"path": path},
        )


__all__ = [
    "AgntSparkError",
    "AuthenticationError",
    "AuthorisationError",
    "EmailAlreadyRegisteredError",
    "NotFoundError",
    "ApiKeyNotFoundError",
    "NotImplementedStubError",
]
