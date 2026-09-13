"""Pins the SDK-compatibility contract: every error response must carry a
`message` key (agntspark-sdk's Client._handle_error reads
`body.get("error", body.get("message", ...))`), and the status codes the
SDK explicitly branches on.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("method", "path", "json", "headers", "expected_status"),
    [
        ("GET", "/v1/auth/me", None, {}, 401),
        ("DELETE", "/v1/api-keys/00000000-0000-0000-0000-000000000000", None, None, 401),
    ],
)
async def test_error_responses_carry_message_key(
    client: AsyncClient, method: str, path: str, json, headers, expected_status: int
) -> None:
    resp = await client.request(method, path, json=json, headers=headers)
    assert resp.status_code == expected_status
    body = resp.json()
    assert "message" in body
    assert "code" in body


async def test_validation_error_is_422_with_message(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/register", json={"email": "not-an-email"})
    assert resp.status_code == 422
    assert "message" in resp.json()
