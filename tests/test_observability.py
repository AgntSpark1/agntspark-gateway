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


async def test_readyz_reports_ready_when_the_database_answers(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_readyz_is_503_when_the_database_is_down() -> None:
    from agntspark_gateway import db as db_module

    class BrokenSession:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise ConnectionRefusedError("database is down")

    async def broken_db():  # type: ignore[no-untyped-def]
        yield BrokenSession()

    app = create_app()
    app.dependency_overrides[db_module.get_db] = broken_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/readyz")

    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}


def test_error_reporting_is_off_without_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from agntspark_gateway import main
    from agntspark_gateway.config import settings

    monkeypatch.setattr(settings, "sentry_dsn", None)
    assert main.init_error_reporting() is False


def test_error_reporting_never_sends_request_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    from agntspark_gateway import main
    from agntspark_gateway.config import settings

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@sentry.example/1")
    monkeypatch.setattr(main.sentry_sdk, "init", lambda **kw: calls.append(kw))

    assert main.init_error_reporting() is True
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["max_request_body_size"] == "never"
