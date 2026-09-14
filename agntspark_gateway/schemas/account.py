"""Schemas for /v1/account/* and /v1/admin/users*."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_serializer, field_validator

from ..plans import PLANS


class PlanLimitsOut(BaseModel):
    max_agents: int
    max_replicas: int
    max_vcpu: float
    max_memory_mb: int
    max_replica_cpu: float
    max_replica_memory_mb: int


class UsageOut(BaseModel):
    agents: int
    replicas: int
    vcpu: float
    memory_mb: int


class PeriodUsageOut(BaseModel):
    """Metered usage since ``start`` (the first of the current month, UTC)."""

    start: datetime
    replica_hours: float
    vcpu_hours: float
    memory_gb_hours: float


class AccountUsage(BaseModel):
    plan: str
    # Admins aren't held to plan limits.
    exempt: bool
    limits: PlanLimitsOut
    usage: UsageOut
    period: PeriodUsageOut


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    plan: str
    is_active: bool
    agents: int
    created_at: datetime

    @field_serializer("id")
    def _serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class AdminUserUpdate(BaseModel):
    role: Literal["viewer", "developer", "admin"] | None = None
    plan: str | None = None
    is_active: bool | None = None

    @field_validator("plan")
    @classmethod
    def _known_plan(cls, v: str | None) -> str | None:
        if v is not None and v not in PLANS:
            raise ValueError(f"Unknown plan {v!r}; expected one of {sorted(PLANS)}")
        return v
