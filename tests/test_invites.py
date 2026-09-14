"""Invite-only registration and the admin invite endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from agntspark_core.auth import Role
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway.config import settings
from agntspark_gateway.models.invite import Invite
from agntspark_gateway.models.user import User
from agntspark_gateway.security.passwords import hash_password

pytestmark = pytest.mark.integration

PASSWORD = "hunter2hunter2"


@pytest.fixture
def invite_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "registration_mode", "invite")


async def _register(client: AsyncClient, email: str, **extra: Any) -> Any:
    return await client.post(
        "/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Invitee", **extra},
    )


async def _user(db: AsyncSession, email: str, role: Role = Role.VIEWER) -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD), name="Seeded", role=int(role))
    db.add(user)
    await db.commit()
    return user


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _admin(client: AsyncClient, db: AsyncSession, email: str) -> dict[str, str]:
    await _user(db, email, Role.ADMIN)
    return await _headers(client, email)


async def _invite(client: AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    resp = await client.post("/v1/admin/invites", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestRegistrationModes:
    async def test_mode_is_published(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert (await client.get("/v1/auth/registration")).json() == {"mode": "open"}
        monkeypatch.setattr(settings, "registration_mode", "invite")
        assert (await client.get("/v1/auth/registration")).json() == {"mode": "invite"}

    async def test_invite_mode_rejects_missing_and_unknown_codes(
        self, client: AsyncClient, invite_only: None
    ) -> None:
        missing = await _register(client, "nocode@agntspark.com")
        unknown = await _register(client, "badcode@agntspark.com", invite_code="inv_nope")
        for resp in (missing, unknown):
            assert resp.status_code == 403
            assert resp.json()["code"] == "INVALID_INVITE"

    async def test_closed_mode_rejects_everyone(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "registration_mode", "closed")
        resp = await _register(client, "closed@agntspark.com")
        assert resp.status_code == 403
        assert resp.json()["code"] == "REGISTRATION_CLOSED"


class TestInvites:
    async def test_single_use_invite(
        self, client: AsyncClient, db_session: AsyncSession, invite_only: None
    ) -> None:
        headers = await _admin(client, db_session, "admin1@agntspark.com")
        invite = await _invite(client, headers, note="first tester")
        assert invite["code"].startswith("inv_")
        assert invite["status"] == "active"

        first = await _register(client, "tester1@agntspark.com", invite_code=invite["code"])
        second = await _register(client, "tester2@agntspark.com", invite_code=invite["code"])

        assert first.status_code == 201
        assert first.json()["user"]["role"] == "viewer"
        assert second.status_code == 403

        [listed] = (await client.get("/v1/admin/invites", headers=headers)).json()
        assert listed["use_count"] == 1
        assert listed["status"] == "used"
        assert "code" not in listed
        assert listed["code_prefix"] == invite["code"][:10]

    async def test_revoked_invite_stops_working(
        self, client: AsyncClient, db_session: AsyncSession, invite_only: None
    ) -> None:
        headers = await _admin(client, db_session, "admin2@agntspark.com")
        invite = await _invite(client, headers, max_uses=5)
        assert (
            await _register(client, "a@agntspark.com", invite_code=invite["code"])
        ).status_code == 201

        revoke = await client.delete(f"/v1/admin/invites/{invite['id']}", headers=headers)
        assert revoke.status_code == 204

        assert (
            await _register(client, "b@agntspark.com", invite_code=invite["code"])
        ).status_code == 403
        [listed] = (await client.get("/v1/admin/invites", headers=headers)).json()
        assert listed["status"] == "revoked"

    async def test_expired_invite_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession, invite_only: None
    ) -> None:
        headers = await _admin(client, db_session, "admin3@agntspark.com")
        invite = await _invite(client, headers, expires_in_days=1)
        row = await db_session.get(Invite, uuid.UUID(invite["id"]))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db_session.commit()

        resp = await _register(client, "late@agntspark.com", invite_code=invite["code"])
        assert resp.status_code == 403

    async def test_failed_registration_does_not_consume_the_invite(
        self, client: AsyncClient, db_session: AsyncSession, invite_only: None
    ) -> None:
        headers = await _admin(client, db_session, "admin4@agntspark.com")
        await _user(db_session, "taken@agntspark.com")
        invite = await _invite(client, headers)

        duplicate = await _register(client, "taken@agntspark.com", invite_code=invite["code"])
        fresh = await _register(client, "fresh@agntspark.com", invite_code=invite["code"])

        assert duplicate.status_code == 409
        assert fresh.status_code == 201

    async def test_revoking_unknown_invite_is_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _admin(client, db_session, "admin5@agntspark.com")
        resp = await client.delete(f"/v1/admin/invites/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    async def test_invite_endpoints_require_admin(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _user(db_session, "viewer@agntspark.com")
        viewer = await _headers(client, "viewer@agntspark.com")

        assert (await client.post("/v1/admin/invites", json={}, headers=viewer)).status_code == 403
        assert (await client.get("/v1/admin/invites", headers=viewer)).status_code == 403
        assert (await client.get("/v1/admin/invites")).status_code == 401
