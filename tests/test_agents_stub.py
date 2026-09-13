import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _token(client: AsyncClient) -> str:
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "agentstub@agntspark.com", "password": "hunter2hunter2", "name": "Stub"},
    )
    return resp.json()["access_token"]


async def test_agents_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/v1/agents")
    assert resp.status_code == 401


async def test_agents_returns_501_when_authenticated(client: AsyncClient) -> None:
    token = await _token(client)
    resp = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 501
    assert resp.json()["code"] == "NOT_IMPLEMENTED"


async def test_agents_nested_path_also_stubbed(client: AsyncClient) -> None:
    token = await _token(client)
    resp = await client.post(
        "/v1/agents/agt_123/deploy", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 501
