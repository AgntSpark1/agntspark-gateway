import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_register_returns_token_and_viewer_role(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "new@agntspark.com", "password": "hunter2hunter2", "name": "New User"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "new@agntspark.com"
    assert body["user"]["role"] == "viewer"


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {"email": "dup@agntspark.com", "password": "hunter2hunter2", "name": "Dup"}
    first = await client.post("/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "short@agntspark.com", "password": "short", "name": "Short"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
