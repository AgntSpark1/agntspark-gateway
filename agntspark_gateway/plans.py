"""Plans and the resource limits that come with them.

Quotas are enforced before anything is created, deployed or scaled — an
action that would take an account past its plan is refused, never billed
after the fact. Admins are exempt. Resource limits count running replicas:
replicas × the per-replica CPU/memory requested in ``DeployConfig``.

The numbers are launch defaults for the invite-only beta, not a pricing
decision; Stripe plans map onto these keys (see billing).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanLimits:
    max_agents: int
    max_replicas: int
    max_vcpu: float
    max_memory_mb: int
    max_replica_cpu: float
    max_replica_memory_mb: int
    # Requests per minute one agent's public URL serves, across all callers.
    max_agent_rpm: int


PLANS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        max_agents=5,
        max_replicas=4,
        max_vcpu=2.0,
        max_memory_mb=2048,
        max_replica_cpu=1.0,
        max_replica_memory_mb=1024,
        max_agent_rpm=600,
    ),
    "pro": PlanLimits(
        max_agents=50,
        max_replicas=50,
        max_vcpu=16.0,
        max_memory_mb=32768,
        max_replica_cpu=4.0,
        max_replica_memory_mb=8192,
        max_agent_rpm=6000,
    ),
}

DEFAULT_PLAN = "free"


def limits_for(plan: str) -> PlanLimits:
    return PLANS.get(plan, PLANS[DEFAULT_PLAN])


__all__ = ["PlanLimits", "PLANS", "DEFAULT_PLAN", "limits_for"]
