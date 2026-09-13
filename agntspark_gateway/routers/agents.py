"""/v1/agents* — real Agent CRUD/deploy/scale/logs/metrics.

Backed by agntspark_core.runtime.AgentRuntime (Docker, single-host). The
DB (`agents` table) is the source of truth for desired config; the runtime
is the source of truth for what's actually running — see
services/agent_service.py for how the two are reconciled.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog
from agntspark_core.runtime import AgentRuntime
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .. import runtime as rt
from ..db import get_db
from ..schemas.agents import (
    AgentConfigIn,
    AgentListResponse,
    AgentResponse,
    DeployConfig,
    LogListResponse,
    Metrics,
    ScaleRequest,
    ScaleResponse,
)
from ..security.dependencies import Principal, get_current_principal
from ..services import agent_service

router = APIRouter(prefix="/v1/agents", tags=["agents"])
log = structlog.get_logger(__name__)


def get_agent_runtime(request: Request) -> AgentRuntime:
    return rt.get_runtime(request)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: AgentConfigIn,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> AgentResponse:
    agent = await agent_service.create_agent(
        db, runtime=runtime, user_id=principal.user_id, body=body
    )
    return agent_service.to_agent_response(agent)


@router.get("", response_model=AgentListResponse)
async def list_agents(
    status: str | None = None,
    tag: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> AgentListResponse:
    agents, total = await agent_service.list_agents(
        db, user_id=principal.user_id, status=status, tag=tag, page=page, page_size=page_size
    )
    return AgentListResponse(
        agents=[agent_service.to_agent_response(a) for a in agents],
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await agent_service.get_agent(db, user_id=principal.user_id, agent_id=agent_id)
    return agent_service.to_agent_response(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> None:
    await agent_service.delete_agent(
        db, runtime=runtime, user_id=principal.user_id, agent_id=agent_id
    )


@router.post("/{agent_id}/deploy", response_model=AgentResponse)
async def deploy_agent(
    agent_id: str,
    body: DeployConfig | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> AgentResponse:
    agent = await agent_service.deploy_agent(
        db, runtime=runtime, user_id=principal.user_id, agent_id=agent_id, deploy=body
    )
    return agent_service.to_agent_response(agent)


@router.post("/{agent_id}/scale", response_model=ScaleResponse)
async def scale_agent(
    agent_id: str,
    body: ScaleRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> ScaleResponse:
    agent, previous_replicas = await agent_service.scale_agent(
        db,
        runtime=runtime,
        user_id=principal.user_id,
        agent_id=agent_id,
        direction=body.direction.value,
        count=body.count,
    )
    return ScaleResponse(
        agent_id=agent.id,
        previous_replicas=previous_replicas,
        current_replicas=agent.replicas,
        direction=body.direction,
        status=agent.status,
    )


@router.get("/{agent_id}/logs", response_model=LogListResponse)
async def get_logs(
    agent_id: str,
    level: str | None = None,
    replica_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> LogListResponse:
    logs = await agent_service.get_logs(
        db,
        runtime=runtime,
        user_id=principal.user_id,
        agent_id=agent_id,
        level=level,
        replica_id=replica_id,
        limit=limit,
    )
    # Simple tail-based pagination for the MVP — no persistent log store yet,
    # so there's nothing to page beyond what Docker's own tail buffer holds.
    return LogListResponse(logs=logs, total=len(logs), has_next=False, next_cursor=None)


@router.get("/{agent_id}/logs/stream")
async def stream_logs(
    request: Request,
    agent_id: str,
    level: str | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> EventSourceResponse:
    await agent_service.get_agent(
        db, user_id=principal.user_id, agent_id=agent_id
    )  # auth/ownership check

    async def _generator() -> AsyncIterator[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        while True:
            if await request.is_disconnected():
                break
            try:
                logs = await agent_service.get_logs(
                    db,
                    runtime=runtime,
                    user_id=principal.user_id,
                    agent_id=agent_id,
                    level=level,
                    replica_id=None,
                    limit=200,
                )
            except Exception as exc:  # keep the stream alive across transient errors
                log.warning("log stream poll failed", agent_id=agent_id, error=str(exc))
                logs = []

            for entry in logs:
                key = (entry.replica_id, entry.timestamp.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                yield {"event": "log", "data": entry.model_dump_json()}

            yield {"event": "heartbeat", "data": "{}"}
            await asyncio.sleep(2)

    return EventSourceResponse(_generator())


@router.get("/{agent_id}/metrics", response_model=Metrics)
async def get_metrics(
    agent_id: str,
    window: str = "1h",
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> Metrics:
    return await agent_service.get_metrics(
        db, runtime=runtime, user_id=principal.user_id, agent_id=agent_id
    )


@router.get("/{agent_id}/metrics/stream")
async def stream_metrics(
    request: Request,
    agent_id: str,
    interval: int = Query(default=10, ge=5),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> EventSourceResponse:
    await agent_service.get_agent(
        db, user_id=principal.user_id, agent_id=agent_id
    )  # auth/ownership check

    async def _generator() -> AsyncIterator[dict[str, Any]]:
        while True:
            if await request.is_disconnected():
                break
            try:
                metrics = await agent_service.get_metrics(
                    db, runtime=runtime, user_id=principal.user_id, agent_id=agent_id
                )
                yield {"event": "metric", "data": metrics.model_dump_json()}
            except Exception as exc:
                log.warning("metric stream poll failed", agent_id=agent_id, error=str(exc))
                yield {"event": "heartbeat", "data": "{}"}
            await asyncio.sleep(interval)

    return EventSourceResponse(_generator())
