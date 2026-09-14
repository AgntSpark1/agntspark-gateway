"""Usage metering (services/metering_service.py) and the usage endpoint's period totals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from agntspark_core.auth import Role
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway.models.agent import Agent
from agntspark_gateway.models.usage_hour import UsageHour
from agntspark_gateway.models.user import User
from agntspark_gateway.security.passwords import hash_password
from agntspark_gateway.services import metering_service

pytestmark = pytest.mark.integration

PASSWORD = "hunter2hunter2"


async def _user_and_agent(db: AsyncSession, email: str, agent_id: str) -> tuple[User, Agent]:
    user = User(
        email=email, password_hash=hash_password(PASSWORD), name="Meter", role=int(Role.OPERATOR)
    )
    db.add(user)
    await db.commit()
    agent = Agent(
        id=agent_id,
        slug=f"{agent_id.replace('_', '-')}-x",
        user_id=user.id,
        name="metered",
        cpu=0.5,
        memory_mb=512,
        replicas=2,
        status="running",
    )
    db.add(agent)
    await db.commit()
    return user, agent


async def test_record_accumulates_in_the_hour_bucket(db_session: AsyncSession) -> None:
    _, agent = await _user_and_agent(db_session, "meter1@agntspark.com", "agt_meter1")
    at = datetime(2026, 9, 14, 10, 15, tzinfo=UTC)

    await metering_service.record(db_session, agent=agent, replicas=2, seconds=30, at=at)
    await metering_service.record(
        db_session, agent=agent, replicas=2, seconds=30, at=at + timedelta(minutes=20)
    )
    await db_session.commit()

    rows = (
        (await db_session.execute(select(UsageHour).where(UsageHour.agent_id == agent.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.hour_start == datetime(2026, 9, 14, 10, tzinfo=UTC)
    assert row.replica_seconds == 120
    assert row.vcpu_seconds == 60  # 2 replicas × 0.5 vCPU × 60 s
    assert row.memory_mb_seconds == 61_440  # 2 × 512 MB × 60 s


async def test_hours_bucket_separately_and_totals_add_up(db_session: AsyncSession) -> None:
    user, agent = await _user_and_agent(db_session, "meter2@agntspark.com", "agt_meter2")
    at = datetime(2026, 9, 14, 10, 50, tzinfo=UTC)

    await metering_service.record(db_session, agent=agent, replicas=1, seconds=3600, at=at)
    await metering_service.record(
        db_session, agent=agent, replicas=1, seconds=1800, at=at + timedelta(minutes=20)
    )
    await db_session.commit()

    month = await metering_service.period_totals(
        db_session, user_id=user.id, start=datetime(2026, 9, 1, tzinfo=UTC)
    )
    assert month.replica_hours == 1.5
    assert month.vcpu_hours == 0.75
    assert month.memory_gb_hours == 0.75  # 0.5 GB × 1.5 h

    later = await metering_service.period_totals(
        db_session, user_id=user.id, start=datetime(2026, 9, 14, 11, tzinfo=UTC)
    )
    assert later.replica_hours == 0.5


async def test_nothing_recorded_without_running_replicas(db_session: AsyncSession) -> None:
    user, agent = await _user_and_agent(db_session, "meter3@agntspark.com", "agt_meter3")
    await metering_service.record(
        db_session, agent=agent, replicas=0, seconds=30, at=datetime.now(UTC)
    )
    await db_session.commit()

    totals = await metering_service.period_totals(
        db_session, user_id=user.id, start=metering_service.month_start()
    )
    assert totals.replica_hours == 0


async def test_usage_endpoint_reports_this_month(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, agent = await _user_and_agent(db_session, "meter4@agntspark.com", "agt_meter4")
    await metering_service.record(
        db_session, agent=agent, replicas=2, seconds=1800, at=datetime.now(UTC)
    )
    await db_session.commit()

    login = await client.post(
        "/v1/auth/login", json={"email": "meter4@agntspark.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    body = (await client.get("/v1/account/usage", headers=headers)).json()

    assert body["period"]["vcpu_hours"] == 0.5  # 2 × 0.5 vCPU × 0.5 h
    assert body["period"]["start"].startswith(metering_service.month_start().date().isoformat())
