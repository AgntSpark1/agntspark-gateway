"""agents table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("runtime", sa.String(32), nullable=False, server_default="python3.12"),
        sa.Column("framework", sa.String(32), nullable=False, server_default="custom"),
        sa.Column("model", sa.String(128), nullable=False, server_default="gpt-4o"),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("agent_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("replicas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cpu", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("gpu", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gpu_type", sa.String(64), nullable=True),
        sa.Column("disk_gb", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("ephemeral_storage_gb", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("env", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("image", sa.String(512), nullable=True),
        sa.Column("build_path", sa.String(512), nullable=True),
        sa.Column("command", sa.String(512), nullable=True),
        sa.Column("args", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("health_check_path", sa.String(256), nullable=True),
        sa.Column("auto_scale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_replicas", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_replicas", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("port", sa.Integer(), nullable=False, server_default="8080"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])


def downgrade() -> None:
    op.drop_table("agents")
