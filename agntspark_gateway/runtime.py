"""Bridges the gateway's async request handlers to agntspark_core's
synchronous, Docker-backed AgentRuntime.

A single AgentRuntime instance lives on ``app.state`` for the process's
lifetime (its container tracking is in-memory — see runtime.reconcile()).
Every method on AgentRuntime is a blocking docker-py call, so everything
here runs it via asyncio.to_thread() to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import structlog
from agntspark_core.config import AgentConfig as CoreAgentConfig
from agntspark_core.config import LLMConfig, LLMProvider
from agntspark_core.config import ResourceLimits as CoreResourceLimits
from agntspark_core.config import RuntimeSettings as CoreRuntimeSettings
from agntspark_core.runtime import AgentRuntime, ContainerInfo, DeploymentStatus
from fastapi import Request

from .config import settings

log = structlog.get_logger(__name__)


def build_core_runtime() -> AgentRuntime:
    core_settings = CoreRuntimeSettings(
        docker_socket=settings.docker_socket,
        docker_image=settings.docker_image,
        docker_network=settings.docker_network,
        auth_enabled=False,  # gateway does its own auth; core's X-API-Key layer is unused here
    )
    return AgentRuntime(settings=core_settings)


async def startup_reconcile(runtime: AgentRuntime) -> None:
    """Rebuild in-memory container tracking from live Docker state.

    Runs once at gateway startup so a restart doesn't orphan agents that
    were already deployed by a previous process.
    """
    try:
        found = await asyncio.to_thread(runtime.reconcile)
        log.info("Runtime reconciled", agents_found=found)
    except Exception as exc:
        log.warning("Runtime reconciliation skipped (Docker unavailable?)", error=str(exc))


def get_runtime(request: Request) -> AgentRuntime:
    return cast(AgentRuntime, request.app.state.agent_runtime)


def _to_core_config(
    *,
    agent_id: str,
    name: str,
    model: str,
    system_prompt: str | None,
    resources: dict[str, Any] | None,
) -> CoreAgentConfig:
    llm_provider = LLMProvider.OPENAI
    llm = LLMConfig(provider=llm_provider, model=model or "gpt-4o")

    core_resources = CoreResourceLimits()
    if resources:
        cpu = resources.get("cpu")
        memory_mb = resources.get("memory_mb")
        if cpu is not None:
            core_resources.cpu_limit = str(cpu)
            core_resources.cpu_request = str(cpu)
        if memory_mb is not None:
            core_resources.memory_limit = f"{int(memory_mb)}m"
            core_resources.memory_request = f"{int(memory_mb)}m"

    return CoreAgentConfig(
        name=name,
        agent_id=agent_id,
        system_prompt=system_prompt or "You are a helpful AI assistant.",
        llm=llm,
        resources=core_resources,
    )


async def deploy_agent(
    runtime: AgentRuntime,
    *,
    agent_id: str,
    name: str,
    model: str,
    system_prompt: str | None,
    replicas: int,
    env: dict[str, str],
    image: str | None,
    resources: dict[str, Any] | None,
) -> list[ContainerInfo]:
    config = _to_core_config(
        agent_id=agent_id, name=name, model=model, system_prompt=system_prompt, resources=resources
    )
    return await asyncio.to_thread(runtime.deploy, config, replicas=replicas, env=env, image=image)


async def stop_agent(runtime: AgentRuntime, agent_id: str) -> list[str]:
    return await asyncio.to_thread(runtime.stop, agent_id)


async def remove_agent(runtime: AgentRuntime, agent_id: str) -> list[str]:
    return await asyncio.to_thread(runtime.remove, agent_id, force=True)


async def health_check(runtime: AgentRuntime, agent_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(runtime.health_check, agent_id)


async def get_logs(
    runtime: AgentRuntime, agent_id: str, *, replica_id: str | None, tail: int
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(runtime.get_logs, agent_id, replica_id=replica_id, tail=tail)


async def scale_agent(
    runtime: AgentRuntime, agent_id: str, direction: str, count: int
) -> DeploymentStatus:
    return await asyncio.to_thread(runtime.scale, agent_id, direction, count)


async def auto_scale(runtime: AgentRuntime, agent_id: str) -> DeploymentStatus:
    return await asyncio.to_thread(runtime.auto_scale, agent_id)


def get_containers(runtime: AgentRuntime, agent_id: str) -> list[ContainerInfo]:
    return runtime.get_containers(agent_id)


def known_agent_ids(runtime: AgentRuntime) -> list[str]:
    return list(runtime.list_all().keys())


__all__ = [
    "build_core_runtime",
    "startup_reconcile",
    "get_runtime",
    "deploy_agent",
    "stop_agent",
    "remove_agent",
    "health_check",
    "get_logs",
    "scale_agent",
    "auto_scale",
    "get_containers",
    "known_agent_ids",
]
