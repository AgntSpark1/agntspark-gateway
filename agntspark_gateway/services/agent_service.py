"""Agent CRUD + deploy/scale/logs/metrics business logic.

Bridges the `agents` DB table (identity + desired config) with the live
Docker state tracked by agntspark_core's AgentRuntime (via
`agntspark_gateway.runtime`). The DB is the source of truth for *what the
agent should be*; the runtime is the source of truth for *what's actually
running*.
"""

from __future__ import annotations

import re
import secrets
import string
import uuid
from datetime import UTC, datetime
from typing import Any

from agntspark_core.exceptions import ScalingError as CoreScalingError
from agntspark_core.runtime import AgentRuntime
from agntspark_core.runtime import ContainerNotFoundError as CoreContainerNotFoundError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime as rt
from ..config import settings
from ..exceptions import AgentNotFoundError, BuildNotSupportedError, DeploymentFailedError
from ..models.agent import Agent
from ..schemas.agents import (
    AgentConfigIn,
    AgentLog,
    AgentResponse,
    DeployConfig,
    EnvVar,
    Metrics,
    ResourceLimits,
)
from ..security.secrets import decrypt_secret, encrypt_secret

_SECRET_MASK = "***"


def _new_agent_id() -> str:
    return f"agt_{secrets.token_urlsafe(15)}"


_SLUG_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def _new_slug(name: str) -> str:
    """``<name-ish>-<6 random chars>``, always a valid DNS label (<= 47 chars).

    The random suffix keeps two agents with the same name apart and makes
    hostnames unguessable from the name alone; names with no ASCII
    letters/digits (e.g. Chinese) fall back to ``agent``.
    """
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "agent"
    suffix = "".join(secrets.choice(_SLUG_SUFFIX_ALPHABET) for _ in range(6))
    return f"{base}-{suffix}"


def _public_url(agent: Agent) -> str | None:
    if not settings.agent_base_domain:
        return None
    return f"https://{agent.slug}.{settings.agent_base_domain}"


def _env_to_storage(env: list[EnvVar]) -> list[dict[str, Any]]:
    return [
        {
            "key": e.key,
            "value": encrypt_secret(e.value) if e.secret else e.value,
            "secret": e.secret,
        }
        for e in env
    ]


def _env_from_storage_masked(stored: list[dict[str, Any]]) -> list[EnvVar]:
    return [
        EnvVar(
            key=e["key"],
            value=_SECRET_MASK if e.get("secret") else e.get("value", ""),
            secret=e.get("secret", False),
        )
        for e in stored
    ]


def _env_for_container(stored: list[dict[str, Any]]) -> dict[str, str]:
    plain: dict[str, str] = {}
    for e in stored:
        value = decrypt_secret(e["value"]) if e.get("secret") else e.get("value", "")
        plain[e["key"]] = value
    return plain


def _apply_deploy_config(agent: Agent, deploy: DeployConfig) -> None:
    agent.replicas = deploy.replicas
    agent.cpu = deploy.resources.cpu
    agent.memory_mb = deploy.resources.memory_mb
    agent.gpu = deploy.resources.gpu
    agent.gpu_type = deploy.resources.gpu_type
    agent.disk_gb = deploy.resources.disk_gb
    agent.ephemeral_storage_gb = deploy.resources.ephemeral_storage_gb
    agent.env = _env_to_storage(deploy.env)
    agent.image = deploy.image
    agent.build_path = deploy.build_path
    agent.command = deploy.command
    agent.args = deploy.args
    agent.health_check_path = deploy.health_check_path
    agent.auto_scale = deploy.auto_scale
    agent.min_replicas = deploy.min_replicas
    agent.max_replicas = deploy.max_replicas
    agent.port = deploy.port


def _deploy_config_from_agent(agent: Agent) -> DeployConfig:
    return DeployConfig(
        replicas=agent.replicas,
        resources=ResourceLimits(
            cpu=agent.cpu,
            memory_mb=agent.memory_mb,
            gpu=agent.gpu,
            gpu_type=agent.gpu_type,
            disk_gb=agent.disk_gb,
            ephemeral_storage_gb=agent.ephemeral_storage_gb,
        ),
        env=_env_from_storage_masked(agent.env),
        image=agent.image,
        build_path=agent.build_path,
        command=agent.command,
        args=agent.args,
        health_check_path=agent.health_check_path,
        auto_scale=agent.auto_scale,
        min_replicas=agent.min_replicas,
        max_replicas=agent.max_replicas,
        port=agent.port,
    )


def to_agent_response(agent: Agent) -> AgentResponse:
    # An agent with neither image nor build_path has never had a deploy
    # config applied (see create_agent) — DeployConfig itself requires one
    # of the two, so there's nothing valid to construct yet.
    has_deploy_config = agent.image is not None or agent.build_path is not None
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        runtime=agent.runtime,
        framework=agent.framework,
        model=agent.model,
        status=agent.status,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        url=_public_url(agent),
        deploy=_deploy_config_from_agent(agent) if has_deploy_config else None,
        tags=agent.tags,
        metadata=agent.agent_metadata,
        error=agent.error,
        version=agent.version,
        replicas=agent.replicas,
    )


async def create_agent(
    db: AsyncSession, *, runtime: AgentRuntime, user_id: uuid.UUID, body: AgentConfigIn
) -> Agent:
    agent = Agent(
        id=_new_agent_id(),
        slug=_new_slug(body.name),
        user_id=user_id,
        name=body.name,
        runtime=body.runtime.value,
        framework=body.framework,
        model=body.model,
        system_prompt=body.system_prompt,
        tags=body.tags,
        agent_metadata=body.metadata,
        status="pending",
    )
    if body.deploy is not None:
        _apply_deploy_config(agent, body.deploy)

    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    if body.deploy is not None:
        agent = await deploy_agent(
            db, runtime=runtime, user_id=user_id, agent_id=agent.id, deploy=None
        )

    return agent


async def _get_owned(db: AsyncSession, *, user_id: uuid.UUID, agent_id: str) -> Agent:
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AgentNotFoundError(agent_id)
    return agent


async def get_agent(db: AsyncSession, *, user_id: uuid.UUID, agent_id: str) -> Agent:
    return await _get_owned(db, user_id=user_id, agent_id=agent_id)


async def list_agents(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    status: str | None,
    tag: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Agent], int]:
    conditions = [Agent.user_id == user_id]
    if status:
        conditions.append(Agent.status == status)
    if tag:
        conditions.append(Agent.tags.any(tag))  # type: ignore[arg-type]  # ARRAY.any() stub is overly strict

    count_result = await db.execute(select(func.count()).select_from(Agent).where(*conditions))
    total = count_result.scalar_one()

    result = await db.execute(
        select(Agent)
        .where(*conditions)
        .order_by(Agent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def deploy_agent(
    db: AsyncSession,
    *,
    runtime: AgentRuntime,
    user_id: uuid.UUID,
    agent_id: str,
    deploy: DeployConfig | None,
) -> Agent:
    agent = await _get_owned(db, user_id=user_id, agent_id=agent_id)

    if deploy is not None:
        _apply_deploy_config(agent, deploy)
        await db.commit()
        await db.refresh(agent)

    if agent.image is None and agent.build_path is not None:
        raise BuildNotSupportedError()

    agent.status = "starting"
    await db.commit()

    try:
        containers = await rt.deploy_agent(
            runtime,
            agent_id=agent.id,
            name=agent.name,
            model=agent.model,
            system_prompt=agent.system_prompt,
            replicas=agent.replicas,
            env=_env_for_container(agent.env),
            image=agent.image,
            resources={"cpu": agent.cpu, "memory_mb": agent.memory_mb},
        )
    except Exception as exc:
        agent.status = "failed"
        agent.error = str(exc)
        await db.commit()
        await db.refresh(agent)
        raise DeploymentFailedError(agent.id, str(exc)) from exc

    agent.status = "running"
    agent.replicas = len(containers)
    agent.error = None
    agent.version += 1
    await db.commit()
    await db.refresh(agent)
    return agent


async def delete_agent(
    db: AsyncSession, *, runtime: AgentRuntime, user_id: uuid.UUID, agent_id: str
) -> None:
    agent = await _get_owned(db, user_id=user_id, agent_id=agent_id)
    await rt.remove_agent(runtime, agent.id)
    await db.delete(agent)
    await db.commit()


async def scale_agent(
    db: AsyncSession,
    *,
    runtime: AgentRuntime,
    user_id: uuid.UUID,
    agent_id: str,
    direction: str,
    count: int,
) -> tuple[Agent, int]:
    agent = await _get_owned(db, user_id=user_id, agent_id=agent_id)
    previous_replicas = agent.replicas

    agent.status = "scaling"
    await db.commit()

    try:
        await rt.scale_agent(runtime, agent.id, direction, count)
    except (CoreScalingError, CoreContainerNotFoundError) as exc:
        agent.status = "failed"
        agent.error = str(exc)
        await db.commit()
        await db.refresh(agent)
        raise DeploymentFailedError(agent.id, str(exc)) from exc

    live_containers = rt.get_containers(runtime, agent.id)
    agent.replicas = len(live_containers) or agent.replicas
    agent.status = "running"
    await db.commit()
    await db.refresh(agent)
    return agent, previous_replicas


async def get_logs(
    db: AsyncSession,
    *,
    runtime: AgentRuntime,
    user_id: uuid.UUID,
    agent_id: str,
    level: str | None,
    replica_id: str | None,
    limit: int,
) -> list[AgentLog]:
    agent = await _get_owned(db, user_id=user_id, agent_id=agent_id)

    try:
        raw_entries = await rt.get_logs(runtime, agent.id, replica_id=replica_id, tail=limit)
    except CoreContainerNotFoundError:
        return []

    logs = [
        AgentLog(
            agent_id=agent.id,
            replica_id=e["replica_id"],
            timestamp=e["timestamp"],
            level="INFO",
            message=e["message"],
            source="stdout",
        )
        for e in raw_entries
    ]

    if level:
        logs = [
            line
            for line in logs
            if level.lower() in line.message.lower() or level.upper() == line.level
        ]

    return logs[-limit:]


async def get_metrics(
    db: AsyncSession, *, runtime: AgentRuntime, user_id: uuid.UUID, agent_id: str
) -> Metrics:
    """Best-effort live snapshot from Docker stats.

    Request-level metrics (request_count/rate, latency percentiles,
    error_count/rate) require an in-path request proxy that doesn't exist
    yet — those fields report 0 until that's built. CPU/memory/replica
    counts are real, live numbers from Docker.
    """
    agent = await _get_owned(db, user_id=user_id, agent_id=agent_id)
    health = await rt.health_check(runtime, agent.id)

    containers = health.get("containers", [])
    n = len(containers) or 1
    avg_cpu = sum(c.get("cpu_percent", 0.0) for c in containers) / n
    avg_mem = sum(c.get("memory_mb", 0.0) for c in containers) / n
    mem_percent = min(100.0, (avg_mem / agent.memory_mb) * 100.0) if agent.memory_mb else 0.0

    return Metrics(
        agent_id=agent.id,
        timestamp=datetime.now(UTC),
        cpu_percent=round(avg_cpu, 2),
        memory_mb=int(avg_mem),
        memory_percent=round(mem_percent, 2),
        replicas=len(containers),
    )


__all__ = [
    "create_agent",
    "get_agent",
    "list_agents",
    "deploy_agent",
    "delete_agent",
    "scale_agent",
    "get_logs",
    "get_metrics",
    "to_agent_response",
]
