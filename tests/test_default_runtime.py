"""Deploying onto the platform's default runtime image (agntspark/agent-runtime):
no image required, provider inferred from the model, bring-your-own LLM key
injected as a secret, and template-friendly system prompts.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "hunter2hunter2", "name": "Runtime Owner"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _container_env(mock_docker: MagicMock) -> dict[str, str]:
    env: dict[str, str] = mock_docker.containers.run.call_args.kwargs["environment"]
    return env


async def _create(
    client: AsyncClient,
    mock_docker: MagicMock,
    make_container: Any,
    *,
    email: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    mock_docker.containers.run.return_value = make_container("aaa111", "agntspark-agt-0", "x")
    resp = await client.post("/v1/agents", json=body, headers=await _headers(client, email))
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestDefaultRuntime:
    async def test_deploy_without_image_uses_runtime_image(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create(
            client,
            mock_docker,
            make_container,
            email="rt1@agntspark.com",
            body={"name": "bot", "deploy": {}},
        )
        assert agent["status"] == "running"
        assert agent["deploy"]["image"] is None
        assert (
            mock_docker.containers.run.call_args.kwargs["image"] == "agntspark/agent-runtime:latest"
        )

    async def test_provider_inferred_from_model(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        await _create(
            client,
            mock_docker,
            make_container,
            email="rt2@agntspark.com",
            body={"name": "claude-bot", "model": "claude-sonnet-4-5", "deploy": {}},
        )
        env = _container_env(mock_docker)
        assert env["LLM_PROVIDER"] == "anthropic"
        assert env["LLM_MODEL"] == "claude-sonnet-4-5"

    async def test_unset_system_prompt_is_passed_empty(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        await _create(
            client,
            mock_docker,
            make_container,
            email="rt3@agntspark.com",
            body={"name": "bot", "deploy": {}},
        )
        assert _container_env(mock_docker)["SYSTEM_PROMPT"] == ""


class TestBringYourOwnKey:
    async def test_api_key_becomes_masked_secret_env(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create(
            client,
            mock_docker,
            make_container,
            email="byok1@agntspark.com",
            body={
                "name": "byok",
                "model": "claude-sonnet-4-5",
                "api_key": "sk-ant-test-123",
                "deploy": {"env": [{"key": "LOG_LEVEL", "value": "debug"}]},
            },
        )

        assert {"key": "ANTHROPIC_API_KEY", "value": "***", "secret": True} in agent["deploy"][
            "env"
        ]
        assert "sk-ant-test-123" not in str(agent)
        env = _container_env(mock_docker)
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-test-123"
        assert env["LOG_LEVEL"] == "debug"

    async def test_redeploy_keeps_the_key(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        headers = await _headers(client, "byok2@agntspark.com")
        mock_docker.containers.run.return_value = make_container("aaa111", "agntspark-agt-0", "x")
        create = await client.post(
            "/v1/agents",
            json={"name": "byok", "api_key": "sk-openai-test", "deploy": {}},
            headers=headers,
        )
        agent_id = create.json()["id"]

        redeploy = await client.post(
            f"/v1/agents/{agent_id}/deploy",
            json={"env": [{"key": "FEATURE", "value": "on"}]},
            headers=headers,
        )

        assert redeploy.status_code == 200, redeploy.text
        env = _container_env(mock_docker)
        assert env["OPENAI_API_KEY"] == "sk-openai-test"
        assert env["FEATURE"] == "on"
