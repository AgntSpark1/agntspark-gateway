"""Internal endpoints the edge proxy (Caddy) calls to serve per-agent ingress.

``https://<slug>.<agent_base_domain>`` is terminated by Caddy, which:

1. asks ``/internal/ingress/tls-ask?domain=...`` before issuing an on-demand
   certificate, so only hostnames of real agents get certificates (random
   subdomains can't burn through Let's Encrypt rate limits), and
2. calls ``/internal/ingress/route`` (``forward_auth``) on every request to
   learn which container to proxy to, via the ``X-Agnt-Upstream`` header.
   That call is also where requests are rate limited, checked against a
   private agent's access keys, and counted for metering.

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
from ..models.user import User
from ..plans import limits_for
from ..security import ingress_limits
from ..services import metering_service, quota_service
from ..services.access_key_service import is_valid_access_key
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


async def _agent_and_owner(db: AsyncSession, host: str) -> tuple[Agent, User] | None:
    slug = slug_from_host(host)
    if slug is None:
        return None
    result = await db.execute(
        select(Agent, User).join(User, User.id == Agent.user_id).where(Agent.slug == slug)
    )
    row = result.one_or_none()
    return None if row is None else (row[0], row[1])


def _bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _too_many_requests(retry_after: int) -> Response:
    return PlainTextResponse(
        "Too many requests to this agent. Try again shortly.\n",
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


@router.get("/tls-ask")
async def tls_ask(domain: str = "", db: AsyncSession = Depends(get_db)) -> Response:
    agent = await _agent_for_host(db, domain)
    return Response(status_code=200 if agent is not None else 404)


@router.get("/route")
async def route(
    x_agnt_host: str = Header(default=""),
    x_agnt_internal_token: str = Header(default=""),
    x_agnt_client_ip: str = Header(default=""),
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> Response:
    # forward_auth relays any non-2xx response to the visitor as-is, so these
    # bodies are what someone opening the agent's URL actually sees.
    expected = settings.ingress_internal_token
    if not expected or not hmac.compare_digest(x_agnt_internal_token, expected):
        return Response(status_code=403)

    found = await _agent_and_owner(db, x_agnt_host)
    if found is None:
        return PlainTextResponse("No agent is deployed at this address.\n", status_code=404)
    agent, owner = found

    # Per caller before the key check, so strangers hammering a private agent
    # spend their own allowance rather than the one its real callers share.
    client_limit = agent.rate_limit_rpm or settings.ingress_default_rpm_per_ip
    retry_after = ingress_limits.per_client.hit(
        f"{agent.id}|{x_agnt_client_ip or 'unknown'}", client_limit
    )
    if retry_after is not None:
        return _too_many_requests(retry_after)

    if agent.access == "private" and not await is_valid_access_key(
        db, agent_id=agent.id, raw_key=_bearer_token(authorization)
    ):
        return PlainTextResponse(
            "This agent is private. Send one of its access keys as "
            "'Authorization: Bearer <key>'.\n",
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="agent"'},
        )

    if not quota_service.is_exempt(owner):
        retry_after = ingress_limits.per_agent.hit(agent.id, limits_for(owner.plan).max_agent_rpm)
        if retry_after is not None:
            return _too_many_requests(retry_after)

    upstreams = [c for c in rt.get_containers(runtime, agent.id) if c.ip_address]
    if not upstreams:
        return PlainTextResponse("This agent is not running.\n", status_code=503)

    metering_service.count_request(agent)

    # Random pick is the whole load balancer for now: good enough across a
    # handful of replicas, and stateless across gateway restarts.
    target = secrets.choice(upstreams)
    return Response(
        status_code=204, headers={"X-Agnt-Upstream": f"{target.ip_address}:{agent.port}"}
    )
