"""Background scheduler loop — the Control Plane's auto-scaler/health-monitor.

Runs as an asyncio task for the gateway process's lifetime. Every
`scheduler_interval_seconds`, it health-checks every agent the DB thinks is
running/scaling, updates their status/replica count from live Docker state,
and evaluates auto-scaling for agents that opted in (`auto_scale=True`).

This is intentionally a single-process, in-memory-runtime-backed loop —
matching AgentRuntime's own single-host design (see runtime.py's docstring).
Running the gateway itself as more than one replica would need each
replica's AgentRuntime reconciled against the same Docker host, which this
does handle (reconcile() matches by label), but two schedulers racing to
scale the same agent is not guarded against — acceptable for the current
single-instance deployment target.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import structlog
from agntspark_core.runtime import AgentRuntime
from sqlalchemy import select

from . import runtime as rt
from .config import settings
from .db import AsyncSessionLocal
from .models.agent import Agent
from .services import metering_service, quota_service

log = structlog.get_logger(__name__)

_ACTIVE_STATUSES = ("running", "scaling", "starting")


async def _tick(runtime: AgentRuntime, elapsed_seconds: float | None = None) -> None:
    # Metered time for this tick: the gap since the previous tick as measured
    # by the loop, or the configured interval on the first tick.
    seconds = (
        elapsed_seconds if elapsed_seconds is not None else settings.scheduler_interval_seconds
    )
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        try:
            await metering_service.flush_requests(db)
        except Exception as exc:
            # The counts stay pending for the next tick.
            log.warning("scheduler: flushing request counts failed", error=str(exc))

        result = await db.execute(select(Agent).where(Agent.status.in_(_ACTIVE_STATUSES)))
        agents = list(result.scalars().all())

        for agent in agents:
            try:
                health = await rt.health_check(runtime, agent.id)
            except Exception as exc:
                log.warning("scheduler: health_check failed", agent_id=agent.id, error=str(exc))
                continue

            live_replicas = len(health.get("containers", []))
            healthy = health.get("healthy", False)

            changed = False
            if live_replicas != agent.replicas:
                agent.replicas = live_replicas
                changed = True

            new_status = agent.status
            if live_replicas == 0:
                new_status = "stopped"
            elif not healthy and health.get("consecutive_failures", 0) >= 3:
                new_status = "crashed"
            elif agent.status != "scaling":
                new_status = "running"

            if new_status != agent.status:
                agent.status = new_status
                changed = True

            if changed:
                await db.commit()

            if agent.status in ("running", "scaling") and live_replicas > 0:
                await metering_service.record(
                    db, agent=agent, replicas=live_replicas, seconds=seconds, at=now
                )
                await db.commit()

            if agent.auto_scale and agent.status == "running" and live_replicas > 0:
                if not await quota_service.has_headroom_for_replica(db, agent):
                    # Auto-scaling never takes an account past its plan.
                    continue
                try:
                    await rt.auto_scale(runtime, agent.id)
                except Exception as exc:
                    log.warning("scheduler: auto_scale failed", agent_id=agent.id, error=str(exc))


async def run_scheduler_loop(runtime: AgentRuntime) -> None:
    interval = settings.scheduler_interval_seconds
    log.info("Scheduler loop starting", interval_seconds=interval)
    last_tick: float | None = None
    while True:
        started = time.monotonic()
        # Capped so a stalled tick isn't metered as a long stretch of usage.
        elapsed = None if last_tick is None else min(started - last_tick, 3 * interval)
        last_tick = started
        try:
            await _tick(runtime, elapsed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Scheduler tick failed", error=str(exc))
        await asyncio.sleep(settings.scheduler_interval_seconds)


__all__ = ["run_scheduler_loop"]
