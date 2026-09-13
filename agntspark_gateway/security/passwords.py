"""Password hashing.

Uses the ``bcrypt`` library directly rather than passlib — passlib is
unmaintained and its bcrypt backend detection breaks against bcrypt>=4.1
(it raises a spurious "password cannot be longer than 72 bytes" error even
for short passwords). bcrypt's own API is small enough not to need a
wrapper library.
"""

from __future__ import annotations

import bcrypt

from ..config import settings


def hash_password(raw_password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(raw_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(raw_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))


__all__ = ["hash_password", "verify_password"]
