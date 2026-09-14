"""usage_hours — hourly per-agent resource usage for billing

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_hours",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("hour_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replica_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vcpu_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("memory_mb_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint("agent_id", "hour_start", name="uq_usage_hours_agent_hour"),
    )
    op.create_index("ix_usage_hours_user_id", "usage_hours", ["user_id"])
    op.create_index("ix_usage_hours_hour_start", "usage_hours", ["hour_start"])


def downgrade() -> None:
    op.drop_table("usage_hours")
