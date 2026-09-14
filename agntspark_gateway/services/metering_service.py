"""Usage metering: running replicas × requested vCPU/memory × time.

The scheduler calls ``record`` for every running agent on each tick. Usage is
what was *reserved* (the replica's requested CPU/memory), not what the process
happened to consume — that's what quotas limit and what a plan sells.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agent import Agent
from ..models.usage_hour import UsageHour


@dataclass(frozen=True)
class PeriodTotals:
    start: datetime
    replica_hours: float
    vcpu_hours: float
    memory_gb_hours: float


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def record(
    db: AsyncSession, *, agent: Agent, replicas: int, seconds: float, at: datetime
) -> None:
    """Add ``seconds`` of ``replicas`` running replicas to the agent's hour bucket.

    Doesn't commit; the caller does.
    """
    if replicas <= 0 or seconds <= 0:
        return
    stmt = insert(UsageHour).values(
        id=uuid.uuid4(),
        user_id=agent.user_id,
        agent_id=agent.id,
        hour_start=at.astimezone(UTC).replace(minute=0, second=0, microsecond=0),
        replica_seconds=replicas * seconds,
        vcpu_seconds=replicas * agent.cpu * seconds,
        memory_mb_seconds=replicas * agent.memory_mb * seconds,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_usage_hours_agent_hour",
        set_={
            "replica_seconds": UsageHour.replica_seconds + stmt.excluded.replica_seconds,
            "vcpu_seconds": UsageHour.vcpu_seconds + stmt.excluded.vcpu_seconds,
            "memory_mb_seconds": UsageHour.memory_mb_seconds + stmt.excluded.memory_mb_seconds,
        },
    )
    await db.execute(stmt)


async def period_totals(
    db: AsyncSession, *, user_id: uuid.UUID, start: datetime, end: datetime | None = None
) -> PeriodTotals:
    conditions = [UsageHour.user_id == user_id, UsageHour.hour_start >= start]
    if end is not None:
        conditions.append(UsageHour.hour_start < end)
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageHour.replica_seconds), 0.0),
                func.coalesce(func.sum(UsageHour.vcpu_seconds), 0.0),
                func.coalesce(func.sum(UsageHour.memory_mb_seconds), 0.0),
            ).where(*conditions)
        )
    ).one()
    replica_s, vcpu_s, memory_mb_s = (float(v) for v in row)
    return PeriodTotals(
        start=start,
        replica_hours=round(replica_s / 3600, 4),
        vcpu_hours=round(vcpu_s / 3600, 4),
        memory_gb_hours=round(memory_mb_s / 1024 / 3600, 4),
    )


__all__ = ["PeriodTotals", "month_start", "record", "period_totals"]
