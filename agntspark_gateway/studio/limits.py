"""Per-plan Studio allowances, keyed like ``agntspark_gateway.plans.PLANS``.

The platform pays for the model, so plans sell replies: when the month's
allowance is used up, the assistant stops answering until the next month or
an upgrade. Nothing is billed after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..plans import DEFAULT_PLAN


@dataclass(frozen=True)
class StudioLimits:
    max_assistants: int
    # Assistant replies per calendar month (UTC), test chats included.
    max_messages_month: int
    # Knowledge text across all of the account's assistants.
    max_knowledge_chars: int


# Starting points, to be tuned against real model cost per reply.
STUDIO_LIMITS: dict[str, StudioLimits] = {
    "free": StudioLimits(max_assistants=3, max_messages_month=100, max_knowledge_chars=100_000),
    "pro": StudioLimits(max_assistants=20, max_messages_month=3_000, max_knowledge_chars=2_000_000),
}


def studio_limits_for(plan: str) -> StudioLimits:
    return STUDIO_LIMITS.get(plan, STUDIO_LIMITS[DEFAULT_PLAN])


__all__ = ["StudioLimits", "STUDIO_LIMITS", "studio_limits_for"]
