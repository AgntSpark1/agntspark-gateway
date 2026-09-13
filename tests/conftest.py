"""Shared pytest fixtures.

Integration tests need a real Postgres (see docker-compose.yml / CI). Each
test runs inside a nested transaction that's rolled back afterward, so tests
don't leak state into each other. Docker itself is always mocked — these
are HTTP/service/DB integration tests, not a live-container smoke test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault(
    "AGNTSPARK_GATEWAY_DATABASE_URL",
    "postgresql+asyncpg://agntspark:agntspark@localhost:5432/agntspark_gateway_test",
)
os.environ.setdefault("AGNTSPARK_GATEWAY_JWT_SECRET", "test-secret")

from agntspark_core.runtime import AgentRuntime  # noqa: E402

from agntspark_gateway import db as db_module  # noqa: E402
from agntspark_gateway.db import Base  # noqa: E402
from agntspark_gateway.main import create_app  # noqa: E402
from agntspark_gateway.routers import agents as agents_router  # noqa: E402


def make_mock_container(container_id: str, name: str, agent_id: str, replica: str = "0"):
    """Build a MagicMock standing in for a docker-py Container object."""
    container = MagicMock()
    container.id = container_id + "0" * (64 - len(container_id))
    container.name = name
    container.status = "running"
    container.image.tags = ["agntspark/agent-runtime:latest"]
    container.labels = {
        "agntspark.agent_id": agent_id,
        "agntspark.name": agent_id,
        "agntspark.replica": replica,
    }
    container.attrs = {"NetworkSettings": {"IPAddress": "172.17.0.2", "Ports": {}}}
    container.reload.return_value = None
    container.logs.return_value = b"2026-01-01T00:00:00.000000000Z hello from the agent\n"
    container.stats.return_value = {
        "memory_stats": {"usage": 64 * 1024 * 1024, "limit": 512 * 1024 * 1024},
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200},
            "system_cpu_usage": 1000,
            "online_cpus": 1,
        },
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
    }
    return container


@pytest_asyncio.fixture(scope="session")
async def _engine():
    engine = create_async_engine(db_module.settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine) -> AsyncIterator[AsyncSession]:
    connection = await _engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    session = session_factory()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
def make_container():
    return make_mock_container


@pytest_asyncio.fixture
def mock_docker() -> MagicMock:
    return MagicMock()


@pytest_asyncio.fixture
def agent_runtime(mock_docker: MagicMock) -> AgentRuntime:
    return AgentRuntime(docker_client=mock_docker)


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, agent_runtime: AgentRuntime
) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[db_module.get_db] = _override_get_db
    app.dependency_overrides[agents_router.get_agent_runtime] = lambda: agent_runtime

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
