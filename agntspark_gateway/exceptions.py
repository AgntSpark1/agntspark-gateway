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


class AgentNotFoundError(NotFoundError):
    """Raised when an agent id does not exist or does not belong to the caller."""

    def __init__(self, agent_id: str) -> None:
        super().__init__("Agent", details={"agent_id": agent_id})


class NotImplementedStubError(AgntSparkError):
    """Raised by reserved-but-unbuilt routes."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"{path!r} is reserved by the API contract but not implemented yet.",
            code="NOT_IMPLEMENTED",
            details={"path": path},
        )


class BuildNotSupportedError(AgntSparkError):
    """Raised when a deploy request supplies only build_path (no pre-built image).

    There is no build-from-source pipeline yet — deploys must reference a
    pre-built container image, or omit `image` entirely to use the
    platform's default generic agent-runtime image.
    """

    def __init__(self) -> None:
        super().__init__(
            "Deploying from build_path is not supported yet — provide a pre-built "
            "'image', or omit both 'image' and 'build_path' to use the platform's "
            "default runtime image.",
            code="BUILD_NOT_SUPPORTED",
        )


class RateLimitExceededError(AgntSparkError):
    """Raised when a caller exceeds a Redis-backed rate limit (e.g. login attempts)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "Too many attempts. Please try again later.",
            code="RATE_LIMIT_EXCEEDED",
            details={"retry_after": retry_after},
        )


class RegistrationClosedError(AgntSparkError):
    """Raised on register when registration_mode is "closed"."""

    def __init__(self) -> None:
        super().__init__("Registration is closed.", code="REGISTRATION_CLOSED")


class InvalidInviteError(AgntSparkError):
    """Missing, unknown, revoked, expired or used-up invite code.

    One message for every case, so codes can't be probed for which state
    they're in.
    """

    def __init__(self) -> None:
        super().__init__(
            "A valid invite code is required to create an account.", code="INVALID_INVITE"
        )


class InviteNotFoundError(NotFoundError):
    """Raised when an admin references an invite id that doesn't exist."""

    def __init__(self, invite_id: str) -> None:
        super().__init__("Invite", details={"invite_id": invite_id})


class DeploymentFailedError(AgntSparkError):
    """Raised when the underlying container runtime fails to deploy/scale an agent."""

    def __init__(self, agent_id: str, reason: str) -> None:
        super().__init__(
            f"Deployment failed for agent {agent_id!r}: {reason}",
            code="DEPLOYMENT_FAILED",
            details={"agent_id": agent_id, "reason": reason},
        )


__all__ = [
    "AgntSparkError",
    "AuthenticationError",
    "AuthorisationError",
    "EmailAlreadyRegisteredError",
    "NotFoundError",
    "ApiKeyNotFoundError",
    "AgentNotFoundError",
    "RateLimitExceededError",
    "NotImplementedStubError",
    "BuildNotSupportedError",
    "DeploymentFailedError",
    "RegistrationClosedError",
    "InvalidInviteError",
    "InviteNotFoundError",
]
