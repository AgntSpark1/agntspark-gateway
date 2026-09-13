import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _register(client: AsyncClient, email: str, password: str) -> None:
    resp = await client.post(
        "/v1/auth/register", json={"email": email, "password": password, "name": "Someone"}
    )
    assert resp.status_code == 201


async def test_login_with_correct_credentials(client: AsyncClient) -> None:
    await _register(client, "login@agntspark.com", "correcthorse")
    resp = await client.post(
        "/v1/auth/login", json={"email": "login@agntspark.com", "password": "correcthorse"}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_login_with_wrong_password(client: AsyncClient) -> None:
    await _register(client, "login2@agntspark.com", "correcthorse")
    resp = await client.post(
        "/v1/auth/login", json={"email": "login2@agntspark.com", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTHENTICATION_FAILED"


async def test_login_with_unknown_email(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/login", json={"email": "nobody@agntspark.com", "password": "whatever"}
    )
    assert resp.status_code == 401
    # Same generic message as wrong-password case — no user-enumeration.
    assert resp.json()["code"] == "AUTHENTICATION_FAILED"
