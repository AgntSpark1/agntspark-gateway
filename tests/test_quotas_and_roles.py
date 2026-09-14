"""Plan quotas (plans.py) and role enforcement on agent/API-key/admin routes."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from agntspark_core.auth import Role
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway.models.user import User
from agntspark_gateway.plans import PLANS
from agntspark_gateway.security.passwords import hash_password

pytestmark = pytest.mark.integration

PASSWORD = "hunter2hunter2"
FREE = PLANS["free"]


async def _account(
    client: AsyncClient,
    db: AsyncSession,
    email: str,
    *,
    role: Role = Role.OPERATOR,
    plan: str = "free",
) -> dict[str, str]:
    db.add(
        User(
            email=email, password_hash=hash_password(PASSWORD), name="Q", role=int(role), plan=plan
        )
    )
    await db.commit()
    resp = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _deploy(replicas: int, cpu: float, memory_mb: int = 256) -> dict[str, Any]:
    return {
        "image": "nginx:alpine",
        "replicas": replicas,
        "resources": {"cpu": cpu, "memory_mb": memory_mb},
    }


@pytest.fixture(autouse=True)
def _containers(mock_docker: MagicMock, make_container: Any) -> None:
    mock_docker.containers.run.return_value = make_container("aaa111", "agntspark-q-0", "q")


class TestQuotas:
    async def test_agent_count_limit(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await _account(client, db_session, "count@agntspark.com")
        for i in range(FREE.max_agents):
            resp = await client.post("/v1/agents", json={"name": f"a{i}"}, headers=headers)
            assert resp.status_code == 201

        over = await client.post("/v1/agents", json={"name": "one-too-many"}, headers=headers)
        assert over.status_code == 403
        assert over.json()["code"] == "QUOTA_EXCEEDED"
        assert over.json()["details"]["resource"] == "agents"

    async def test_per_replica_cpu_cap_leaves_nothing_behind(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _account(client, db_session, "cpu@agntspark.com")
        resp = await client.post(
            "/v1/agents",
            json={"name": "big", "deploy": _deploy(1, FREE.max_replica_cpu + 1)},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["details"]["resource"] == "cpu_per_replica"
        assert (await client.get("/v1/agents", headers=headers)).json()["total"] == 0

    async def test_total_vcpu_counts_every_running_agent(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _account(client, db_session, "vcpu@agntspark.com")
        first = await client.post(
            "/v1/agents", json={"name": "first", "deploy": _deploy(2, 1.0)}, headers=headers
        )
        assert first.status_code == 201

        second = await client.post(
            "/v1/agents", json={"name": "second", "deploy": _deploy(1, 0.5)}, headers=headers
        )
        assert second.status_code == 403
        assert second.json()["details"]["resource"] == "vcpu"

    async def test_scale_up_stops_at_the_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _account(client, db_session, "scale@agntspark.com")
        agent = (
            await client.post(
                "/v1/agents", json={"name": "s", "deploy": _deploy(2, 1.0)}, headers=headers
            )
        ).json()

        resp = await client.post(
            f"/v1/agents/{agent['id']}/scale", json={"direction": "up", "count": 1}, headers=headers
        )
        assert resp.status_code == 403
        after = (await client.get(f"/v1/agents/{agent['id']}", headers=headers)).json()
        assert after["replicas"] == 2

    async def test_redeploy_does_not_count_the_agent_twice(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _account(client, db_session, "redeploy@agntspark.com")
        agent = (
            await client.post(
                "/v1/agents", json={"name": "r", "deploy": _deploy(2, 1.0)}, headers=headers
            )
        ).json()

        resp = await client.post(
            f"/v1/agents/{agent['id']}/deploy", json=_deploy(2, 1.0), headers=headers
        )
        assert resp.status_code == 200, resp.text

    async def test_pro_plan_and_admins_get_more(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        pro = await _account(client, db_session, "pro@agntspark.com", plan="pro")
        admin = await _account(client, db_session, "boss@agntspark.com", role=Role.ADMIN)

        pro_resp = await client.post(
            "/v1/agents", json={"name": "p", "deploy": _deploy(3, 2.0)}, headers=pro
        )
        admin_resp = await client.post(
            "/v1/agents", json={"name": "b", "deploy": _deploy(8, 4.0)}, headers=admin
        )
        assert pro_resp.status_code == 201
        assert admin_resp.status_code == 201

    async def test_usage_endpoint(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await _account(client, db_session, "usage@agntspark.com")
        await client.post(
            "/v1/agents", json={"name": "u", "deploy": _deploy(2, 0.5, 512)}, headers=headers
        )

        body = (await client.get("/v1/account/usage", headers=headers)).json()
        assert body["plan"] == "free"
        assert body["exempt"] is False
        assert body["usage"] == {"agents": 1, "replicas": 2, "vcpu": 1.0, "memory_mb": 1024}
        assert body["limits"]["max_agents"] == FREE.max_agents


class TestRoles:
    async def test_register_grants_developer(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/v1/auth/register",
            json={"email": "dev@agntspark.com", "password": PASSWORD, "name": "Dev"},
        )
        assert resp.json()["user"]["role"] == "developer"
        assert resp.json()["user"]["plan"] == "free"

    async def test_viewer_is_read_only(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await _account(client, db_session, "viewer@agntspark.com", role=Role.VIEWER)

        assert (await client.get("/v1/agents", headers=headers)).status_code == 200
        assert (
            await client.post("/v1/agents", json={"name": "x"}, headers=headers)
        ).status_code == 403
        assert (
            await client.post("/v1/api-keys", json={"label": "x"}, headers=headers)
        ).status_code == 403

    async def test_admin_manages_roles_and_plans(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _account(client, db_session, "admin@agntspark.com", role=Role.ADMIN)
        member = await _account(client, db_session, "member@agntspark.com")
        users = (await client.get("/v1/admin/users", headers=admin)).json()
        member_id = next(u["id"] for u in users if u["email"] == "member@agntspark.com")

        resp = await client.patch(
            f"/v1/admin/users/{member_id}", json={"plan": "pro", "role": "viewer"}, headers=admin
        )
        assert resp.status_code == 200
        assert resp.json()["plan"] == "pro"
        assert resp.json()["role"] == "viewer"
        assert (
            await client.post("/v1/agents", json={"name": "x"}, headers=member)
        ).status_code == 403

        bad_plan = await client.patch(
            f"/v1/admin/users/{member_id}", json={"plan": "platinum"}, headers=admin
        )
        assert bad_plan.status_code == 422

    async def test_admin_cannot_lock_themselves_out(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _account(client, db_session, "self@agntspark.com", role=Role.ADMIN)
        me = (await client.get("/v1/auth/me", headers=admin)).json()

        demote = await client.patch(
            f"/v1/admin/users/{me['id']}", json={"role": "developer"}, headers=admin
        )
        deactivate = await client.patch(
            f"/v1/admin/users/{me['id']}", json={"is_active": False}, headers=admin
        )
        assert demote.status_code == 403
        assert deactivate.status_code == 403

    async def test_non_admins_cannot_manage_users(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _account(client, db_session, "nosy@agntspark.com")
        assert (await client.get("/v1/admin/users", headers=headers)).status_code == 403
