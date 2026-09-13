import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_metrics_endpoint_returns_prometheus_format(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    # A metric agntspark_core always registers, regardless of activity.
    assert "agntspark_containers_active" in resp.text


async def test_metrics_reflects_real_deploy_activity(
    client: AsyncClient, mock_docker, make_container
) -> None:
    container = make_container("aaa111", "agntspark-agt-0", "irrelevant")
    mock_docker.containers.run.return_value = container

    register = await client.post(
        "/v1/auth/register",
        json={"email": "metrics-scrape@agntspark.com", "password": "hunter2hunter2", "name": "M"},
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
    await client.post(
        "/v1/agents",
        json={"name": "scraped-bot", "deploy": {"image": "ghcr.io/x/agent:1.0"}},
        headers=headers,
    )

    resp = await client.get("/metrics")
    assert "agntspark_containers_active" in resp.text
