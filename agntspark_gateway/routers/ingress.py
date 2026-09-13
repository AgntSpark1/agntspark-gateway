"""Internal endpoints the edge proxy (Caddy) calls to serve per-agent ingress.

``https://<slug>.<agent_base_domain>`` is terminated by Caddy, which:

1. asks ``/internal/ingress/tls-ask?domain=...`` before issuing an on-demand
   certificate, so only hostnames of real agents get certificates (random
   subdomains can't burn through Let's Encrypt rate limits), and
2. calls ``/internal/ingress/route`` (``forward_auth``) on every request to
   learn which container to proxy to, via the ``X-Agnt-Upstream`` header.

Not public API: the edge only proxies ``/v1/*`` and ``/healthz`` on the API
hostname, and the gateway isn't attached to the agents' network. ``route``
reveals internal container addresses, so it additionally requires the shared
``ingress_internal_token``.
"""

from __future__ import annotations

import hmac
import secrets

from agntspark_core.runtime import AgentRuntime
from fastapi import APIRouter, Depends, Header
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime as rt
from ..config import settings
from ..db import get_db
from ..models.agent import Agent
from .agents import get_agent_runtime

router = APIRouter(prefix="/internal/ingress", tags=["internal"], include_in_schema=False)


def slug_from_host(host: str) -> str | None:
    """``my-bot-x1y2z3.run.example.com[:443]`` → ``my-bot-x1y2z3``, else None."""
    base = settings.agent_base_domain
    if not base:
        return None
    host = host.split(":", 1)[0].rstrip(".").lower()
    suffix = "." + base.lower().rstrip(".")
    if not host.endswith(suffix):
        return None
    slug = host[: -len(suffix)]
    if not slug or "." in slug:
        return None
    return slug


async def _agent_for_host(db: AsyncSession, host: str) -> Agent | None:
    slug = slug_from_host(host)
    if slug is None:
        return None
    result = await db.execute(select(Agent).where(Agent.slug == slug))
    return result.scalar_one_or_none()


@router.get("/tls-ask")
async def tls_ask(domain: str = "", db: AsyncSession = Depends(get_db)) -> Response:
    agent = await _agent_for_host(db, domain)
    return Response(status_code=200 if agent is not None else 404)


@router.get("/route")
async def route(
    x_agnt_host: str = Header(default=""),
    x_agnt_internal_token: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> Response:
    # forward_auth relays any non-2xx response to the visitor as-is, so these
    # bodies are what someone opening the agent's URL actually sees.
    expected = settings.ingress_internal_token
    if not expected or not hmac.compare_digest(x_agnt_internal_token, expected):
        return Response(status_code=403)

    agent = await _agent_for_host(db, x_agnt_host)
    if agent is None:
        return PlainTextResponse("No agent is deployed at this address.\n", status_code=404)

    upstreams = [c for c in rt.get_containers(runtime, agent.id) if c.ip_address]
    if not upstreams:
        return PlainTextResponse("This agent is not running.\n", status_code=503)

    # Random pick is the whole load balancer for now: good enough across a
    # handful of replicas, and stateless across gateway restarts.
    target = secrets.choice(upstreams)
    return Response(
        status_code=204, headers={"X-Agnt-Upstream": f"{target.ip_address}:{agent.port}"}
    )
