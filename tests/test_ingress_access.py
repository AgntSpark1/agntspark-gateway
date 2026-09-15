"""Private agents, ingress rate limits and request metering (routers/ingress.py)."""

from __future__ import annotations

import dataclasses
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway import plans
from agntspark_gateway.config import settings
from agntspark_gateway.security.ingress_limits import FixedWindowLimiter
from agntspark_gateway.services import metering_service

pytestmark = pytest.mark.integration

BASE = "run.test.agntspark.com"
TOKEN = "ingress-test-token"


@pytest.fixture(autouse=True)
def _ingress_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_base_domain", BASE)
    monkeypatch.setattr(settings, "ingress_internal_token", TOKEN)


async def _owner_and_agent(
    client: AsyncClient,
    mock_docker: MagicMock,
    make_container: Any,
    *,
    email: str,
    access: str = "public",
) -> tuple[dict[str, str], dict[str, Any]]:
    register = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "hunter2hunter2", "name": "Owner"},
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
    container = make_container("bbb222", "agntspark-agt-0", "irrelevant")
    container.attrs = {
        "NetworkSettings": {"Networks": {"agntspark-net": {"IPAddress": "172.20.0.9"}}}
    }
    mock_docker.containers.run.return_value = container
    resp = await client.post(
        "/v1/agents",
        json={"name": "bot", "access": access, "deploy": {"image": "nginx:alpine", "port": 80}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return headers, resp.json()


async def _visit(
    client: AsyncClient, agent: dict[str, Any], *, ip: str = "203.0.113.7", key: str | None = None
) -> Any:
    headers = {
        "X-Agnt-Internal-Token": TOKEN,
        "X-Agnt-Host": agent["url"].removeprefix("https://"),
        "X-Agnt-Client-IP": ip,
    }
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return await client.get("/internal/ingress/route", headers=headers)


class TestPrivateAgents:
    async def test_new_agents_are_public_by_default(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        _, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="pub@agntspark.com"
        )
        assert agent["access"] == "public"
        assert agent["rate_limit_rpm"] is None
        assert (await _visit(client, agent)).status_code == 204

    async def test_private_agent_needs_one_of_its_keys(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        headers, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="priv@agntspark.com", access="private"
        )
        other_headers, other = await _owner_and_agent(
            client, mock_docker, make_container, email="priv-other@agntspark.com"
        )
        created = await client.post(
            f"/v1/agents/{agent['id']}/access-keys", json={"label": "web app"}, headers=headers
        )
        other_key = await client.post(
            f"/v1/agents/{other['id']}/access-keys", headers=other_headers
        )
        assert created.status_code == 201
        key = created.json()["key"]
        assert key.startswith("agk_")

        missing = await _visit(client, agent)
        assert missing.status_code == 401
        assert missing.headers["WWW-Authenticate"].startswith("Bearer")
        assert "X-Agnt-Upstream" not in missing.headers
        assert (await _visit(client, agent, key="agk_wrong")).status_code == 401
        assert (await _visit(client, agent, key=other_key.json()["key"])).status_code == 401

        ok = await _visit(client, agent, key=key)
        assert ok.status_code == 204
        assert ok.headers["X-Agnt-Upstream"] == "172.20.0.9:80"

    async def test_keys_are_shown_once_and_revocable(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        headers, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="keys@agntspark.com", access="private"
        )
        created = (
            await client.post(f"/v1/agents/{agent['id']}/access-keys", headers=headers)
        ).json()

        listed = (await client.get(f"/v1/agents/{agent['id']}/access-keys", headers=headers)).json()
        assert listed == [
            {
                "id": created["id"],
                "label": "default",
                "key_preview": created["key"][:12] + "...",
                "created_at": created["created_at"],
            }
        ]

        deleted = await client.delete(
            f"/v1/agents/{agent['id']}/access-keys/{created['id']}", headers=headers
        )
        assert deleted.status_code == 204
        assert (await _visit(client, agent, key=created["key"])).status_code == 401
        again = await client.delete(
            f"/v1/agents/{agent['id']}/access-keys/{created['id']}", headers=headers
        )
        assert again.status_code == 404

    async def test_keys_of_someone_elses_agent_are_not_found(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        _, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="victim@agntspark.com"
        )
        intruder, _ = await _owner_and_agent(
            client, mock_docker, make_container, email="intruder@agntspark.com"
        )
        path = f"/v1/agents/{agent['id']}/access-keys"
        assert (await client.post(path, headers=intruder)).status_code == 404
        assert (await client.get(path, headers=intruder)).status_code == 404

    async def test_patch_switches_access_and_rate_limit(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        headers, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="patch@agntspark.com"
        )
        path = f"/v1/agents/{agent['id']}"

        private = await client.patch(
            path, json={"access": "private", "rate_limit_rpm": 30}, headers=headers
        )
        assert private.status_code == 200
        assert (private.json()["access"], private.json()["rate_limit_rpm"]) == ("private", 30)
        assert (await _visit(client, agent)).status_code == 401

        # Omitting a field leaves it; null restores the default.
        kept = await client.patch(path, json={"access": "public"}, headers=headers)
        assert kept.json()["rate_limit_rpm"] == 30
        cleared = await client.patch(path, json={"rate_limit_rpm": None}, headers=headers)
        assert cleared.json()["rate_limit_rpm"] is None
        assert (await _visit(client, agent)).status_code == 204

        invalid = await client.patch(path, json={"rate_limit_rpm": 0}, headers=headers)
        assert invalid.status_code == 422


class TestRateLimits:
    async def test_per_client_limit(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        headers, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="rpm@agntspark.com"
        )
        await client.patch(f"/v1/agents/{agent['id']}", json={"rate_limit_rpm": 2}, headers=headers)

        assert [(await _visit(client, agent)).status_code for _ in range(2)] == [204, 204]
        limited = await _visit(client, agent)
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["Retry-After"]) <= 60
        # Another caller has its own allowance.
        assert (await _visit(client, agent, ip="198.51.100.1")).status_code == 204

    async def test_unauthorized_callers_dont_spend_the_agents_allowance(
        self,
        client: AsyncClient,
        mock_docker: MagicMock,
        make_container: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(
            plans.PLANS, "free", dataclasses.replace(plans.PLANS["free"], max_agent_rpm=2)
        )
        headers, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="spend@agntspark.com", access="private"
        )
        key = (await client.post(f"/v1/agents/{agent['id']}/access-keys", headers=headers)).json()[
            "key"
        ]

        for i in range(5):
            assert (await _visit(client, agent, ip=f"192.0.2.{i}")).status_code == 401
        assert (await _visit(client, agent, key=key)).status_code == 204

    async def test_plan_caps_an_agent_across_callers(
        self,
        client: AsyncClient,
        mock_docker: MagicMock,
        make_container: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(
            plans.PLANS, "free", dataclasses.replace(plans.PLANS["free"], max_agent_rpm=3)
        )
        _, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="cap@agntspark.com"
        )

        statuses = [(await _visit(client, agent, ip=f"192.0.2.{i}")).status_code for i in range(4)]
        assert statuses == [204, 204, 204, 429]


class TestFixedWindowLimiter:
    def test_counts_per_key_and_resets_each_window(self) -> None:
        now = [120.0]
        limiter = FixedWindowLimiter(window_seconds=60, clock=lambda: now[0])

        assert limiter.hit("a", 2) is None
        assert limiter.hit("a", 2) is None
        assert limiter.hit("b", 2) is None
        now[0] = 150.5
        assert limiter.hit("a", 2) == 30  # window ends at 180

        now[0] = 180.0
        assert limiter.hit("a", 2) is None


class TestRequestMetering:
    async def test_only_routed_requests_are_counted_and_flushed(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        mock_docker: MagicMock,
        make_container: Any,
    ) -> None:
        headers, agent = await _owner_and_agent(
            client, mock_docker, make_container, email="count@agntspark.com", access="private"
        )
        key = (await client.post(f"/v1/agents/{agent['id']}/access-keys", headers=headers)).json()[
            "key"
        ]

        for _ in range(3):
            assert (await _visit(client, agent, key=key)).status_code == 204
        assert (await _visit(client, agent)).status_code == 401
        assert metering_service.pending_requests() == 3

        assert await metering_service.flush_requests(db_session) == 3
        assert metering_service.pending_requests() == 0
        assert (await _visit(client, agent, key=key)).status_code == 204
        assert await metering_service.flush_requests(db_session) == 1

        usage = (await client.get("/v1/account/usage", headers=headers)).json()
        assert usage["period"]["requests"] == 4
        assert usage["limits"]["max_agent_rpm"] == plans.PLANS["free"].max_agent_rpm

    async def test_counts_for_deleted_accounts_are_dropped(self, db_session: AsyncSession) -> None:
        ghost = SimpleNamespace(id="agt_ghost", user_id=uuid.uuid4())
        metering_service.count_request(ghost)  # type: ignore[arg-type]

        assert await metering_service.flush_requests(db_session) == 0
        assert metering_service.pending_requests() == 0

    async def test_failed_flush_keeps_the_counts(self) -> None:
        metering_service.count_request(
            SimpleNamespace(id="agt_retry", user_id=uuid.uuid4())  # type: ignore[arg-type]
        )
        db = AsyncMock()
        db.execute.side_effect = RuntimeError("database is down")

        with pytest.raises(RuntimeError):
            await metering_service.flush_requests(db)
        assert metering_service.pending_requests() == 1
        db.rollback.assert_awaited()
