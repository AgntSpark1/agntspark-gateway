"""Tests for per-agent public URLs and the internal ingress endpoints Caddy
calls (routers/ingress.py). Docker is mocked, as everywhere else.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from agntspark_gateway.config import settings
from agntspark_gateway.routers.ingress import slug_from_host

pytestmark = pytest.mark.integration

BASE = "run.test.agntspark.com"
TOKEN = "ingress-test-token"
INTERNAL = {"X-Agnt-Internal-Token": TOKEN}


@pytest.fixture(autouse=True)
def _ingress_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_base_domain", BASE)
    monkeypatch.setattr(settings, "ingress_internal_token", TOKEN)


async def _create_agent(
    client: AsyncClient,
    mock_docker: MagicMock,
    make_container: Any,
    *,
    email: str,
    name: str = "My Bot!",
    deploy: bool = True,
) -> dict[str, Any]:
    register = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "hunter2hunter2", "name": "Ingress Owner"},
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

    body: dict[str, Any] = {"name": name}
    if deploy:
        container = make_container("aaa111", "agntspark-agt-0", "irrelevant")
        # User-defined networks only report the address under Networks.<name>.
        container.attrs = {
            "NetworkSettings": {
                "IPAddress": "",
                "Ports": {},
                "Networks": {"agntspark-net": {"IPAddress": "172.20.0.5"}},
            }
        }
        mock_docker.containers.run.return_value = container
        body["deploy"] = {"image": "nginx:alpine", "port": 80}

    resp = await client.post("/v1/agents", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _host(agent: dict[str, Any]) -> str:
    return agent["url"].removeprefix("https://")


class TestPublicUrl:
    async def test_url_is_slug_subdomain(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create_agent(client, mock_docker, make_container, email="url1@agntspark.com")
        assert re.fullmatch(r"https://my-bot-[a-z0-9]{6}\.run\.test\.agntspark\.com", agent["url"])

    async def test_non_ascii_name_falls_back_to_agent(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create_agent(
            client,
            mock_docker,
            make_container,
            email="url2@agntspark.com",
            name="客服助手",
            deploy=False,
        )
        assert re.fullmatch(r"https://agent-[a-z0-9]{6}\.run\.test\.agntspark\.com", agent["url"])

    async def test_url_null_when_ingress_disabled(
        self,
        client: AsyncClient,
        mock_docker: MagicMock,
        make_container: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "agent_base_domain", None)
        agent = await _create_agent(client, mock_docker, make_container, email="url3@agntspark.com")
        assert agent["url"] is None


class TestSlugFromHost:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("my-bot-abc123.run.test.agntspark.com", "my-bot-abc123"),
            ("My-Bot-ABC123.run.test.agntspark.com:443", "my-bot-abc123"),
            ("my-bot-abc123.run.test.agntspark.com.", "my-bot-abc123"),
            ("run.test.agntspark.com", None),
            ("a.b.run.test.agntspark.com", None),
            ("my-bot-abc123.evil.com", None),
            ("my-bot-abc123run.test.agntspark.com", None),
        ],
    )
    def test_parsing(self, host: str, expected: str | None) -> None:
        assert slug_from_host(host) == expected


class TestTlsAsk:
    async def test_only_existing_agents_get_certificates(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create_agent(client, mock_docker, make_container, email="tls1@agntspark.com")

        ok = await client.get("/internal/ingress/tls-ask", params={"domain": _host(agent)})
        assert ok.status_code == 200

        for domain in (f"nope-000000.{BASE}", "agntapi.agntspark.com", ""):
            resp = await client.get("/internal/ingress/tls-ask", params={"domain": domain})
            assert resp.status_code == 404, domain


class TestRoute:
    async def test_returns_container_upstream(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create_agent(
            client, mock_docker, make_container, email="route1@agntspark.com"
        )

        resp = await client.get(
            "/internal/ingress/route", headers={**INTERNAL, "X-Agnt-Host": _host(agent)}
        )
        assert resp.status_code == 204
        assert resp.headers["X-Agnt-Upstream"] == "172.20.0.5:80"

    async def test_requires_internal_token(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create_agent(
            client, mock_docker, make_container, email="route2@agntspark.com"
        )
        host = {"X-Agnt-Host": _host(agent)}

        missing = await client.get("/internal/ingress/route", headers=host)
        wrong = await client.get(
            "/internal/ingress/route", headers={**host, "X-Agnt-Internal-Token": "guess"}
        )
        assert missing.status_code == 403
        assert wrong.status_code == 403
        assert "X-Agnt-Upstream" not in wrong.headers

    async def test_rejects_everything_when_token_unconfigured(
        self,
        client: AsyncClient,
        mock_docker: MagicMock,
        make_container: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agent = await _create_agent(
            client, mock_docker, make_container, email="route3@agntspark.com"
        )
        monkeypatch.setattr(settings, "ingress_internal_token", None)

        resp = await client.get(
            "/internal/ingress/route",
            headers={"X-Agnt-Internal-Token": "", "X-Agnt-Host": _host(agent)},
        )
        assert resp.status_code == 403

    async def test_unknown_host_is_404(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/internal/ingress/route", headers={**INTERNAL, "X-Agnt-Host": f"ghost-000000.{BASE}"}
        )
        assert resp.status_code == 404

    async def test_undeployed_agent_is_503(
        self, client: AsyncClient, mock_docker: MagicMock, make_container: Any
    ) -> None:
        agent = await _create_agent(
            client, mock_docker, make_container, email="route4@agntspark.com", deploy=False
        )
        resp = await client.get(
            "/internal/ingress/route", headers={**INTERNAL, "X-Agnt-Host": _host(agent)}
        )
        assert resp.status_code == 503
