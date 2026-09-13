"""Shared pytest fixtures.

Integration tests need a real Postgres (see docker-compose.yml / CI). Each
test runs inside a nested transaction that's rolled back afterward, so tests
don't leak state into each other.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault(
    "AGNTSPARK_GATEWAY_DATABASE_URL",
    "postgresql+asyncpg://agntspark:agntspark@localhost:5432/agntspark_gateway_test",
)
os.environ.setdefault("AGNTSPARK_GATEWAY_JWT_SECRET", "test-secret")

from agntspark_gateway import db as db_module  # noqa: E402
from agntspark_gateway.db import Base  # noqa: E402
from agntspark_gateway.main import create_app  # noqa: E402


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
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[db_module.get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
