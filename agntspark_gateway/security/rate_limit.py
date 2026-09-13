"""Redis-backed rate limiting for the login/register endpoints.

Fixed-window counter keyed by client IP. Fails open (allows the request,
logs a warning) if Redis is unreachable or unconfigured — an MVP posture
that never blocks legitimate users because of a Redis outage, at the cost
of losing brute-force protection during that outage.
"""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog
from fastapi import Request
from redis.exceptions import RedisError

from ..config import settings
from ..exceptions import RateLimitExceededError

log = structlog.get_logger(__name__)

_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis | None:
    global _redis_client
    if not settings.redis_url:
        return None
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def _enforce(key: str) -> None:
    redis = _get_redis()
    if redis is None:
        return

    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.login_rate_limit_window_seconds)
        if count > settings.login_rate_limit_max_attempts:
            ttl = await redis.ttl(key)
            raise RateLimitExceededError(retry_after=max(ttl, 1))
    except RedisError as exc:
        log.warning("rate limiter: Redis unavailable, failing open", error=str(exc))


async def enforce_login_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    await _enforce(f"ratelimit:login:{client_ip}")


__all__ = ["enforce_login_rate_limit"]
