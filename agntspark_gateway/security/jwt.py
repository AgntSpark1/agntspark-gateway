"""JWT issuance and verification.

HS256, short-lived (default 60 min), no refresh tokens in this slice — see
the plan's follow-up notes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from ..config import settings
from ..exceptions import AuthenticationError

ISSUER = "agntspark-gateway"


def create_access_token(*, user_id: uuid.UUID, email: str, role: int) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    now = datetime.now(UTC)
    expires_in = settings.jwt_expire_minutes * 60
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=ISSUER,
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc


__all__ = ["create_access_token", "decode_access_token"]
