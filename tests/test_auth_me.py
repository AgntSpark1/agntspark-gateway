import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_me_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 401


async def test_me_with_valid_jwt(client: AsyncClient) -> None:
    register = await client.post(
        "/v1/auth/register",
        json={"email": "me@agntspark.com", "password": "hunter2hunter2", "name": "Me"},
    )
    token = register.json()["access_token"]

    resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@agntspark.com"


async def test_me_with_garbage_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401
