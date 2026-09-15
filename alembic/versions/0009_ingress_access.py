"""agent access control, ingress rate limits, and request metering

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-15

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing agents stay reachable exactly as before.
    op.add_column(
        "agents", sa.Column("access", sa.String(16), nullable=False, server_default="public")
    )
    op.add_column("agents", sa.Column("rate_limit_rpm", sa.Integer(), nullable=True))
    op.add_column(
        "usage_hours", sa.Column("requests", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.create_table(
        "agent_access_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(32),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_access_keys_agent_id", "agent_access_keys", ["agent_id"])
    op.create_index(
        "ix_agent_access_keys_key_hash", "agent_access_keys", ["key_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_table("agent_access_keys")
    op.drop_column("usage_hours", "requests")
    op.drop_column("agents", "rate_limit_rpm")
    op.drop_column("agents", "access")
