"""Request ids, the catch-all 500 envelope, and the production config guard."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agntspark_gateway.config import GatewaySettings, production_config_problems
from agntspark_gateway.main import create_app


async def test_responses_carry_a_request_id(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert len(resp.headers["x-request-id"]) == 32


async def test_a_sane_caller_request_id_is_kept(client: AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "edge-abc.123"})
    assert resp.headers["x-request-id"] == "edge-abc.123"


async def test_an_unsafe_caller_request_id_is_replaced(client: AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "x" * 65})
    assert resp.headers["x-request-id"] != "x" * 65


async def test_unhandled_errors_return_the_error_envelope() -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail from deep inside")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/boom", headers={"X-Request-ID": "trace-1"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["details"] == {"request_id": "trace-1"}
    assert "secret detail" not in resp.text
    assert resp.headers["x-request-id"] == "trace-1"


def _prod(**overrides: str) -> GatewaySettings:
    values = {
        "environment": "production",
        "jwt_secret": "a" * 64,
        "secret_encryption_key": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyMTI=",
    }
    values.update(overrides)
    return GatewaySettings(_env_file=None, **values)


def test_a_properly_configured_production_passes() -> None:
    assert production_config_problems(_prod()) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"jwt_secret": "dev-secret-change-me"}, "JWT_SECRET"),
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        (
            {"secret_encryption_key": GatewaySettings().secret_encryption_key},
            "SECRET_ENCRYPTION_KEY",
        ),
        ({"stripe_secret_key": "sk_test_x"}, "STRIPE_WEBHOOK_SECRET"),
    ],
)
def test_unsafe_production_config_is_refused(overrides: dict[str, str], fragment: str) -> None:
    problems = production_config_problems(_prod(**overrides))
    assert any(fragment in p for p in problems)


def test_development_is_never_refused() -> None:
    assert production_config_problems(GatewaySettings(_env_file=None)) == []
