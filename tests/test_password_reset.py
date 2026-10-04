"""Forgotten-password reset by email, changing a password, and the sessions
both end."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway.config import settings
from agntspark_gateway.models.password_reset_token import PasswordResetToken
from agntspark_gateway.services import email_service

pytestmark = pytest.mark.integration

PASSWORD = "correcthorse"


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def fake_send(**message: Any) -> None:
        sent.append(message)

    monkeypatch.setattr(email_service, "send_email", fake_send)
    monkeypatch.setattr(settings, "public_base_url", "https://console.test")
    return sent


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "name": "Ada"}
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _token_from(message: dict[str, Any]) -> str:
    match = re.search(r"https://console\.test/reset-password\?token=(\S+)", message["text"])
    assert match, message["text"]
    return match.group(1)


async def _me(client: AsyncClient, token: str) -> int:
    resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return resp.status_code


async def test_reset_by_email_sets_the_new_password_and_ends_sessions(
    client: AsyncClient, outbox: list[dict[str, Any]]
) -> None:
    old_session = await _register(client, "reset@agntspark.com")

    resp = await client.post("/v1/auth/password-reset", json={"email": "reset@agntspark.com"})
    assert resp.status_code == 202
    assert [m["to"] for m in outbox] == ["reset@agntspark.com"]

    token = _token_from(outbox[0])
    confirm = await client.post(
        "/v1/auth/password-reset/confirm", json={"token": token, "password": "newpassword1"}
    )
    assert confirm.status_code == 204

    assert await _me(client, old_session) == 401
    old_login = await client.post(
        "/v1/auth/login", json={"email": "reset@agntspark.com", "password": PASSWORD}
    )
    new_login = await client.post(
        "/v1/auth/login", json={"email": "reset@agntspark.com", "password": "newpassword1"}
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert await _me(client, new_login.json()["access_token"]) == 200


async def test_unknown_emails_get_the_same_reply_and_no_email(
    client: AsyncClient, outbox: list[dict[str, Any]]
) -> None:
    resp = await client.post("/v1/auth/password-reset", json={"email": "nobody@agntspark.com"})
    assert resp.status_code == 202
    assert resp.json() == {"status": "sent"}
    assert outbox == []


async def test_a_reset_link_works_once(client: AsyncClient, outbox: list[dict[str, Any]]) -> None:
    await _register(client, "once@agntspark.com")
    await client.post("/v1/auth/password-reset", json={"email": "once@agntspark.com"})
    await client.post("/v1/auth/password-reset", json={"email": "once@agntspark.com"})
    first, second = _token_from(outbox[0]), _token_from(outbox[1])

    ok = await client.post(
        "/v1/auth/password-reset/confirm", json={"token": second, "password": "newpassword1"}
    )
    again = await client.post(
        "/v1/auth/password-reset/confirm", json={"token": second, "password": "newpassword2"}
    )
    # Using one link also retires the other outstanding one.
    other = await client.post(
        "/v1/auth/password-reset/confirm", json={"token": first, "password": "newpassword3"}
    )

    assert ok.status_code == 204
    assert again.status_code == 400
    assert again.json()["code"] == "INVALID_RESET_TOKEN"
    assert other.status_code == 400


async def test_expired_and_made_up_links_are_refused(
    client: AsyncClient, db_session: AsyncSession, outbox: list[dict[str, Any]]
) -> None:
    await _register(client, "expired@agntspark.com")
    await client.post("/v1/auth/password-reset", json={"email": "expired@agntspark.com"})
    record = (await db_session.execute(select(PasswordResetToken))).scalars().one()
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    expired = await client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": _token_from(outbox[0]), "password": "newpassword1"},
    )
    made_up = await client.post(
        "/v1/auth/password-reset/confirm", json={"token": "nope", "password": "newpassword1"}
    )

    assert expired.status_code == 400
    assert made_up.status_code == 400


async def test_a_failing_mail_server_doesnt_leak_through_the_reply(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "smtpdown@agntspark.com")

    async def broken(**message: Any) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(email_service, "send_email", broken)
    resp = await client.post("/v1/auth/password-reset", json={"email": "smtpdown@agntspark.com"})
    assert resp.status_code == 202


async def test_changing_the_password_keeps_this_session_and_ends_the_others(
    client: AsyncClient,
) -> None:
    other_device = await _register(client, "change@agntspark.com")
    login = await client.post(
        "/v1/auth/login", json={"email": "change@agntspark.com", "password": PASSWORD}
    )
    this_device = login.json()["access_token"]

    wrong = await client.post(
        "/v1/auth/password",
        json={"current_password": "not-it", "new_password": "newpassword1"},
        headers={"Authorization": f"Bearer {this_device}"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "WRONG_PASSWORD"

    changed = await client.post(
        "/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "newpassword1"},
        headers={"Authorization": f"Bearer {this_device}"},
    )
    assert changed.status_code == 200

    assert await _me(client, changed.json()["access_token"]) == 200
    assert await _me(client, this_device) == 401
    assert await _me(client, other_device) == 401


async def test_api_keys_cannot_change_the_password(client: AsyncClient) -> None:
    session = await _register(client, "keychange@agntspark.com")
    key = await client.post(
        "/v1/api-keys", json={"label": "ci"}, headers={"Authorization": f"Bearer {session}"}
    )
    raw_key = key.json()["key"]

    resp = await client.post(
        "/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "newpassword1"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code == 401
