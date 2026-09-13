import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _register_and_get_token(client: AsyncClient, email: str) -> str:
    resp = await client.post(
        "/v1/auth/register", json={"email": email, "password": "hunter2hunter2", "name": "Key User"}
    )
    return resp.json()["access_token"]


async def test_create_list_and_use_api_key(client: AsyncClient) -> None:
    jwt = await _register_and_get_token(client, "keys@agntspark.com")
    headers = {"Authorization": f"Bearer {jwt}"}

    create_resp = await client.post(
        "/v1/api-keys", json={"label": "CI key", "scopes": ["agent:read"]}, headers=headers
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["key"].startswith("agnt_")
    assert body["keyPreview"]
    raw_key = body["key"]

    list_resp = await client.get("/v1/api-keys", headers=headers)
    assert list_resp.status_code == 200
    assert any(k["id"] == body["id"] for k in list_resp.json())

    # The freshly minted API key itself should authenticate like a JWT would.
    me_resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {raw_key}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "keys@agntspark.com"


async def test_api_key_cannot_mint_another_key(client: AsyncClient) -> None:
    jwt = await _register_and_get_token(client, "nofarm@agntspark.com")
    create_resp = await client.post(
        "/v1/api-keys",
        json={"label": "first", "scopes": []},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    raw_key = create_resp.json()["key"]

    farm_resp = await client.post(
        "/v1/api-keys",
        json={"label": "farmed", "scopes": []},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert farm_resp.status_code == 401


async def test_revoke_api_key(client: AsyncClient) -> None:
    jwt = await _register_and_get_token(client, "revoke@agntspark.com")
    headers = {"Authorization": f"Bearer {jwt}"}

    create_resp = await client.post(
        "/v1/api-keys", json={"label": "to revoke", "scopes": []}, headers=headers
    )
    key_id = create_resp.json()["id"]
    raw_key = create_resp.json()["key"]

    delete_resp = await client.delete(f"/v1/api-keys/{key_id}", headers=headers)
    assert delete_resp.status_code == 204

    # A revoked key must no longer authenticate.
    me_resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {raw_key}"})
    assert me_resp.status_code == 401


async def test_delete_nonexistent_key_is_404(client: AsyncClient) -> None:
    jwt = await _register_and_get_token(client, "notfound@agntspark.com")
    resp = await client.delete(
        "/v1/api-keys/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 404
