"""Usage metering: running replicas × requested vCPU/memory × time, and requests.

The scheduler calls ``record`` for every running agent on each tick. Usage is
what was *reserved* (the replica's requested CPU/memory), not what the process
happened to consume — that's what quotas limit and what a plan sells.

Requests are counted in memory as the edge routes them (``count_request``, on
the hot path of every call to an agent) and written out by the scheduler
(``flush_requests``), so routing never waits on a database write. Counts not
yet flushed are lost if the process dies: at most one scheduler interval.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agent import Agent
from ..models.usage_hour import UsageHour
from ..models.user import User

# (agent_id, user_id, hour_start) → requests not yet written.
_pending_requests: Counter[tuple[str, uuid.UUID, datetime]] = Counter()


@dataclass(frozen=True)
class PeriodTotals:
    start: datetime
    replica_hours: float
    vcpu_hours: float
    memory_gb_hours: float
    requests: int


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _hour_start(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


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
        hour_start=_hour_start(at),
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


def count_request(agent: Agent, at: datetime | None = None) -> None:
    """Count one request routed to ``agent``, in memory until the next flush."""
    _pending_requests[(agent.id, agent.user_id, _hour_start(at or datetime.now(UTC)))] += 1


def pending_requests() -> int:
    return sum(_pending_requests.values())


def discard_pending_requests() -> None:
    _pending_requests.clear()


async def flush_requests(db: AsyncSession) -> int:
    """Write pending request counts into their hour buckets and commit.

    Returns the number of requests written. On failure the counts are put
    back, so the next flush retries them.
    """
    # No await between copying and clearing, so no request is counted twice
    # or dropped by a concurrent count_request.
    batch = dict(_pending_requests)
    _pending_requests.clear()
    if not batch:
        return 0

    try:
        # Counts for an account deleted since would fail the foreign key on
        # every retry; they have nobody left to bill.
        user_ids = {user_id for _, user_id, _ in batch}
        existing = set(
            (await db.execute(select(User.id).where(User.id.in_(user_ids)))).scalars().all()
        )
        written = 0
        for (agent_id, user_id, hour_start), count in batch.items():
            if user_id not in existing:
                continue
            stmt = insert(UsageHour).values(
                id=uuid.uuid4(),
                user_id=user_id,
                agent_id=agent_id,
                hour_start=hour_start,
                requests=count,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_usage_hours_agent_hour",
                set_={"requests": UsageHour.requests + stmt.excluded.requests},
            )
            await db.execute(stmt)
            written += count
        await db.commit()
    except Exception:
        await db.rollback()
        _pending_requests.update(batch)
        raise
    return written


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
                func.coalesce(func.sum(UsageHour.requests), 0),
            ).where(*conditions)
        )
    ).one()
    replica_s, vcpu_s, memory_mb_s = (float(v) for v in row[:3])
    return PeriodTotals(
        start=start,
        replica_hours=round(replica_s / 3600, 4),
        vcpu_hours=round(vcpu_s / 3600, 4),
        memory_gb_hours=round(memory_mb_s / 1024 / 3600, 4),
        requests=int(row[3]),
    )


__all__ = [
    "PeriodTotals",
    "count_request",
    "discard_pending_requests",
    "flush_requests",
    "month_start",
    "pending_requests",
    "period_totals",
    "record",
]
