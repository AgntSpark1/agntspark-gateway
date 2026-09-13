"""Tests for /v1/agents* — real CRUD/deploy/scale/logs/metrics, with the
Docker client mocked (see conftest.make_container / agent_runtime).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _token(client: AsyncClient, email: str = "agents@agntspark.com") -> str:
    resp = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "hunter2hunter2", "name": "Agent Owner"},
    )
    return resp.json()["access_token"]


async def _auth_headers(client: AsyncClient, email: str = "agents@agntspark.com") -> dict[str, str]:
    token = await _token(client, email)
    return {"Authorization": f"Bearer {token}"}


class TestCreate:
    async def test_create_without_deploy_stays_pending(
        self, client: AsyncClient, mock_docker: MagicMock
    ) -> None:
        headers = await _auth_headers(client)
        resp = await client.post("/v1/agents", json={"name": "my-bot"}, headers=headers)

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["replicas"] == 0
        mock_docker.containers.run.assert_not_called()

    async def test_create_with_deploy_starts_container(
        self, client: AsyncClient, mock_docker: MagicMock, make_container
    ) -> None:
        container = make_container("abc123", "agntspark-agt-0", "irrelevant")
        mock_docker.containers.run.return_value = container
        headers = await _auth_headers(client)

        resp = await client.post(
            "/v1/agents",
            json={"name": "my-bot", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
            headers=headers,
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "running"
        assert body["replicas"] == 1
        assert mock_docker.containers.run.call_args.kwargs["image"] == "ghcr.io/x/agent:1.0"

    async def test_create_deploy_failure_returns_error_and_marks_failed(
        self, client: AsyncClient, mock_docker: MagicMock
    ) -> None:
        mock_docker.containers.run.side_effect = RuntimeError("no space left on device")
        headers = await _auth_headers(client)

        resp = await client.post(
            "/v1/agents",
            json={"name": "my-bot", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
            headers=headers,
        )

        assert resp.status_code >= 400
        assert resp.json()["code"] == "DEPLOYMENT_FAILED"

        list_resp = await client.get("/v1/agents", headers=headers)
        agents = list_resp.json()["agents"]
        assert any(a["status"] == "failed" for a in agents)

    async def test_build_path_without_image_is_not_supported(self, client: AsyncClient) -> None:
        headers = await _auth_headers(client)
        resp = await client.post(
            "/v1/agents",
            json={"name": "my-bot", "deploy": {"build_path": "https://github.com/x/y.git"}},
            headers=headers,
        )
        assert resp.status_code == 501
        assert resp.json()["code"] == "BUILD_NOT_SUPPORTED"

    async def test_create_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post("/v1/agents", json={"name": "my-bot"})
        assert resp.status_code == 401


class TestListAndGet:
    async def test_list_only_shows_own_agents(self, client: AsyncClient) -> None:
        headers_a = await _auth_headers(client, "owner-a@agntspark.com")
        headers_b = await _auth_headers(client, "owner-b@agntspark.com")

        await client.post("/v1/agents", json={"name": "a-bot"}, headers=headers_a)

        list_b = await client.get("/v1/agents", headers=headers_b)
        assert list_b.json()["total"] == 0

        list_a = await client.get("/v1/agents", headers=headers_a)
        assert list_a.json()["total"] == 1

    async def test_get_other_users_agent_is_404(self, client: AsyncClient) -> None:
        headers_a = await _auth_headers(client, "owner-c@agntspark.com")
        headers_b = await _auth_headers(client, "owner-d@agntspark.com")

        create = await client.post("/v1/agents", json={"name": "c-bot"}, headers=headers_a)
        agent_id = create.json()["id"]

        resp = await client.get(f"/v1/agents/{agent_id}", headers=headers_b)
        assert resp.status_code == 404


class TestDeployAndScale:
    async def test_deploy_endpoint_redeploys_pending_agent(
        self, client: AsyncClient, mock_docker: MagicMock, make_container
    ) -> None:
        headers = await _auth_headers(client, "deploy1@agntspark.com")
        create = await client.post("/v1/agents", json={"name": "deploy-me"}, headers=headers)
        agent_id = create.json()["id"]

        container = make_container("aaa111", "agntspark-agt-0", "irrelevant")
        mock_docker.containers.run.return_value = container

        resp = await client.post(
            f"/v1/agents/{agent_id}/deploy",
            json={"image": "ghcr.io/x/agent:1.0"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    async def test_scale_up_increases_replicas(
        self, client: AsyncClient, mock_docker: MagicMock, make_container
    ) -> None:
        headers = await _auth_headers(client, "scale1@agntspark.com")
        c0 = make_container("aaa111", "agntspark-agt-0", "irrelevant", "0")
        c1 = make_container("bbb222", "agntspark-agt-1", "irrelevant", "1")
        mock_docker.containers.run.side_effect = [c0, c1]

        create = await client.post(
            "/v1/agents",
            json={"name": "scale-me", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
            headers=headers,
        )
        agent_id = create.json()["id"]
        assert create.json()["replicas"] == 1

        resp = await client.post(
            f"/v1/agents/{agent_id}/scale", json={"direction": "up", "count": 1}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["previous_replicas"] == 1
        assert body["current_replicas"] == 2

    async def test_scale_requires_existing_deployment(self, client: AsyncClient) -> None:
        headers = await _auth_headers(client, "scale2@agntspark.com")
        create = await client.post("/v1/agents", json={"name": "not-deployed"}, headers=headers)
        agent_id = create.json()["id"]

        resp = await client.post(
            f"/v1/agents/{agent_id}/scale", json={"direction": "up", "count": 1}, headers=headers
        )
        assert resp.status_code >= 400


class TestDelete:
    async def test_delete_removes_agent(
        self, client: AsyncClient, mock_docker: MagicMock, make_container
    ) -> None:
        headers = await _auth_headers(client, "delete1@agntspark.com")
        container = make_container("aaa111", "agntspark-agt-0", "irrelevant")
        mock_docker.containers.run.return_value = container
        mock_docker.containers.get.return_value = container

        create = await client.post(
            "/v1/agents",
            json={"name": "delete-me", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
            headers=headers,
        )
        agent_id = create.json()["id"]

        delete_resp = await client.delete(f"/v1/agents/{agent_id}", headers=headers)
        assert delete_resp.status_code == 204

        get_resp = await client.get(f"/v1/agents/{agent_id}", headers=headers)
        assert get_resp.status_code == 404


class TestLogsAndMetrics:
    async def test_get_logs_returns_parsed_lines(
        self, client: AsyncClient, mock_docker: MagicMock, make_container
    ) -> None:
        headers = await _auth_headers(client, "logs1@agntspark.com")
        container = make_container("aaa111", "agntspark-agt-0", "irrelevant")
        mock_docker.containers.run.return_value = container
        mock_docker.containers.get.return_value = container

        create = await client.post(
            "/v1/agents",
            json={"name": "log-me", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
            headers=headers,
        )
        agent_id = create.json()["id"]

        resp = await client.get(f"/v1/agents/{agent_id}/logs", headers=headers)
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["message"] == "hello from the agent"

    async def test_get_metrics_returns_live_stats(
        self, client: AsyncClient, mock_docker: MagicMock, make_container
    ) -> None:
        headers = await _auth_headers(client, "metrics1@agntspark.com")
        container = make_container("aaa111", "agntspark-agt-0", "irrelevant")
        mock_docker.containers.run.return_value = container
        mock_docker.containers.get.return_value = container

        create = await client.post(
            "/v1/agents",
            json={"name": "metrics-me", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
            headers=headers,
        )
        agent_id = create.json()["id"]

        resp = await client.get(f"/v1/agents/{agent_id}/metrics", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["replicas"] == 1
        assert body["memory_mb"] == 64  # from the mocked container.stats()
        assert body["cpu_percent"] > 0

    async def test_logs_requires_ownership(self, client: AsyncClient) -> None:
        headers_a = await _auth_headers(client, "logs-owner@agntspark.com")
        headers_b = await _auth_headers(client, "logs-intruder@agntspark.com")
        create = await client.post("/v1/agents", json={"name": "private-bot"}, headers=headers_a)
        agent_id = create.json()["id"]

        resp = await client.get(f"/v1/agents/{agent_id}/logs", headers=headers_b)
        assert resp.status_code == 404
