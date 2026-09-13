"""Tests for the Redis-backed login rate limiter.

Uses fakeredis (an in-memory redis-protocol-compatible fake) instead of a
real Redis server — CI/local dev don't need one running for these tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from httpx import AsyncClient
from redis.exceptions import RedisError

from agntspark_gateway.security import rate_limit as rl

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rl, "_redis_client", fake)
    monkeypatch.setattr(rl.settings, "redis_url", "redis://fake:6379/0")
    monkeypatch.setattr(rl.settings, "login_rate_limit_max_attempts", 3)
    monkeypatch.setattr(rl.settings, "login_rate_limit_window_seconds", 60)
    yield fake


async def test_login_allowed_under_the_limit(client: AsyncClient) -> None:
    for _ in range(3):
        resp = await client.post(
            "/v1/auth/login", json={"email": "nope@agntspark.com", "password": "wrong"}
        )
        assert resp.status_code == 401  # wrong creds, but not rate-limited yet


async def test_login_blocked_over_the_limit(client: AsyncClient) -> None:
    for _ in range(3):
        await client.post(
            "/v1/auth/login", json={"email": "nope2@agntspark.com", "password": "wrong"}
        )

    resp = await client.post(
        "/v1/auth/login", json={"email": "nope2@agntspark.com", "password": "wrong"}
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in resp.headers


async def test_rate_limit_is_per_ip_not_per_email(client: AsyncClient) -> None:
    # httpx's ASGITransport test client always presents the same client IP,
    # so hitting the limit for one email also blocks a different email from
    # the "same" caller — this test documents that IP-scoped behavior.
    for _ in range(3):
        await client.post("/v1/auth/login", json={"email": "a@agntspark.com", "password": "x"})

    resp = await client.post("/v1/auth/login", json={"email": "b@agntspark.com", "password": "x"})
    assert resp.status_code == 429


async def test_redis_failure_fails_open(client: AsyncClient, monkeypatch) -> None:
    broken_redis = MagicMock()
    broken_redis.incr = AsyncMock(side_effect=RedisError("connection refused"))
    monkeypatch.setattr(rl, "_get_redis", lambda: broken_redis)

    resp = await client.post(
        "/v1/auth/login", json={"email": "failopen@agntspark.com", "password": "wrong"}
    )
    # Redis is broken, but login should still be reachable (401 for bad
    # creds, not 500/429) — fail-open, not fail-closed.
    assert resp.status_code == 401
