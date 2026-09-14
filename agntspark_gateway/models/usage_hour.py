"""Hourly resource usage per agent — the basis for usage-based billing.

One row per (agent, hour), accumulated by the scheduler: running replicas ×
the replica's requested vCPU / memory × seconds observed. No foreign key to
agents, so usage outlives a deleted agent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class UsageHour(Base):
    __tablename__ = "usage_hours"
    __table_args__ = (UniqueConstraint("agent_id", "hour_start", name="uq_usage_hours_agent_hour"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    hour_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    replica_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vcpu_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    memory_mb_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
