"""Unified Bearer authentication.

The public API accepts ``Authorization: Bearer <token>`` where ``<token>``
is either a JWT (browser/console session) or a long-lived API key (SDK/CLI).
The ``agnt_`` prefix unambiguously distinguishes the two, since API keys are
never valid JWTs and vice versa by construction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agntspark_core.auth import Role
from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..exceptions import AuthenticationError, AuthorisationError
from ..models.api_key import ApiKey
from ..models.user import User
from .api_keys import hash_key
from .jwt import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller — either a user session (JWT) or an API key."""

    user_id: uuid.UUID
    email: str
    role: Role
    auth_method: str  # "jwt" | "api_key"
    api_key_id: uuid.UUID | None = None


async def _authenticate_api_key(token: str, db: AsyncSession) -> Principal:
    key_hash = hash_key(token)
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()
    if api_key is None or not api_key.is_active:
        raise AuthenticationError("Invalid or revoked API key.")

    user = await db.get(User, api_key.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or revoked API key.")

    return Principal(
        user_id=user.id,
        email=user.email,
        role=user.role_enum,
        auth_method="api_key",
        api_key_id=api_key.id,
    )


async def _authenticate_jwt(token: str, db: AsyncSession) -> Principal:
    payload = decode_access_token(token)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid token payload.") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired token.")

    return Principal(user_id=user.id, email=user.email, role=user.role_enum, auth_method="jwt")


async def get_current_principal(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if creds is None or not creds.credentials:
        raise AuthenticationError("Missing Authorization header.")

    token = creds.credentials
    if token.startswith(f"{settings.api_key_prefix}_"):
        return await _authenticate_api_key(token, db)
    return await _authenticate_jwt(token, db)


async def require_jwt_principal(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Like get_current_principal, but rejects API keys.

    Used for /v1/api-keys (POST) so API keys can't mint further API keys.
    """
    if creds is None or not creds.credentials:
        raise AuthenticationError("Missing Authorization header.")
    token = creds.credentials
    if token.startswith(f"{settings.api_key_prefix}_"):
        raise AuthenticationError("This action requires a logged-in session, not an API key.")
    return await _authenticate_jwt(token, db)


def require_role(minimum: Role) -> Callable[..., Any]:
    """Return a FastAPI dependency that enforces a minimum role."""

    async def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.role.can(minimum):
            raise AuthorisationError(str(principal.user_id), f"require_role({minimum.name})")
        return principal

    return _check


__all__ = [
    "Principal",
    "get_current_principal",
    "require_jwt_principal",
    "require_role",
]
