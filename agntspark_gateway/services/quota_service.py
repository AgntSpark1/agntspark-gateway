"""Per-account quotas from the account's plan (see plans.py).

Checks run before the DB or Docker is touched, so a refused action leaves
nothing behind. They read current usage and then act without a lock: two
concurrent requests could each fit and together overshoot slightly, which is
acceptable for invite-only usage.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from agntspark_core.auth import Role
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import AuthenticationError, QuotaExceededError
from ..models.agent import Agent
from ..models.user import User
from ..plans import PlanLimits, limits_for

# Agents in these states hold their replicas' resources.
ACTIVE_STATUSES = ("running", "scaling", "starting")


@dataclass(frozen=True)
class Usage:
    agents: int
    replicas: int
    vcpu: float
    memory_mb: int


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationError("Invalid or expired token.")
    return user


def is_exempt(user: User) -> bool:
    return user.role_enum.can(Role.ADMIN)


async def current_usage(
    db: AsyncSession, user_id: uuid.UUID, *, exclude_agent_id: str | None = None
) -> Usage:
    agents = (
        await db.execute(select(func.count()).select_from(Agent).where(Agent.user_id == user_id))
    ).scalar_one()

    conditions = [Agent.user_id == user_id, Agent.status.in_(ACTIVE_STATUSES)]
    if exclude_agent_id is not None:
        conditions.append(Agent.id != exclude_agent_id)
    active = (await db.execute(select(Agent).where(*conditions))).scalars().all()

    return Usage(
        agents=agents,
        replicas=sum(a.replicas for a in active),
        vcpu=round(sum(a.replicas * a.cpu for a in active), 3),
        memory_mb=sum(a.replicas * a.memory_mb for a in active),
    )


async def usage_report(db: AsyncSession, *, user_id: uuid.UUID) -> tuple[User, PlanLimits, Usage]:
    user = await _user(db, user_id)
    return user, limits_for(user.plan), await current_usage(db, user_id)


async def ensure_can_create_agent(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    user = await _user(db, user_id)
    if is_exempt(user):
        return
    limits = limits_for(user.plan)
    usage = await current_usage(db, user_id)
    if usage.agents + 1 > limits.max_agents:
        raise QuotaExceededError(
            "agents", plan=user.plan, limit=limits.max_agents, current=usage.agents, requested=1
        )


async def ensure_resources(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    agent_id: str | None,
    replicas: int,
    cpu: float,
    memory_mb: int,
) -> None:
    """Would running ``agent_id`` with this shape stay within the plan?

    The agent's own current usage is excluded, so redeploying or resizing it
    isn't counted twice.
    """
    user = await _user(db, user_id)
    if is_exempt(user):
        return
    limits = limits_for(user.plan)

    if cpu > limits.max_replica_cpu:
        raise QuotaExceededError(
            "cpu_per_replica", plan=user.plan, limit=limits.max_replica_cpu, requested=cpu
        )
    if memory_mb > limits.max_replica_memory_mb:
        raise QuotaExceededError(
            "memory_mb_per_replica",
            plan=user.plan,
            limit=limits.max_replica_memory_mb,
            requested=memory_mb,
        )

    usage = await current_usage(db, user_id, exclude_agent_id=agent_id)
    checks: list[tuple[str, float, float, float]] = [
        ("replicas", usage.replicas, replicas, limits.max_replicas),
        ("vcpu", usage.vcpu, round(replicas * cpu, 3), limits.max_vcpu),
        ("memory_mb", usage.memory_mb, replicas * memory_mb, limits.max_memory_mb),
    ]
    for resource, current, requested, limit in checks:
        if round(current + requested, 3) > limit:
            raise QuotaExceededError(
                resource, plan=user.plan, limit=limit, current=current, requested=requested
            )


async def has_headroom_for_replica(db: AsyncSession, agent: Agent) -> bool:
    try:
        await ensure_resources(
            db,
            user_id=agent.user_id,
            agent_id=agent.id,
            replicas=agent.replicas + 1,
            cpu=agent.cpu,
            memory_mb=agent.memory_mb,
        )
    except QuotaExceededError:
        return False
    return True


__all__ = [
    "ACTIVE_STATUSES",
    "Usage",
    "current_usage",
    "usage_report",
    "ensure_can_create_agent",
    "ensure_resources",
    "has_headroom_for_replica",
    "is_exempt",
]
