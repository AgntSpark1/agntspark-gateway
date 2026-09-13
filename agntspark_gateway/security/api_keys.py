"""API key generation and verification.

Reuses ``agntspark_core.auth.APIKey`` for the actual crypto (the
``agnt_<token_urlsafe(32)>`` format, SHA-256 hashing, and constant-time
comparison) rather than re-deriving it — only the storage backend differs
(Postgres here vs. the in-memory ``APIKeyStore`` in agntspark-core).
"""

from __future__ import annotations

import hashlib
import hmac

from agntspark_core.auth import APIKey, Role


def mint_api_key(principal: str, role: Role) -> tuple[str, str, str]:
    """Create a new API key.

    Returns ``(raw_key, key_hash, key_prefix)``. ``raw_key`` is shown to the
    caller exactly once and never stored.
    """
    key_obj, raw_key = APIKey.generate(principal, roles=[role])
    key_prefix = raw_key[:12]
    return raw_key, key_obj.key_hash, key_prefix


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def keys_match(raw_key: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_key(raw_key), expected_hash)


__all__ = ["mint_api_key", "hash_key", "keys_match"]
