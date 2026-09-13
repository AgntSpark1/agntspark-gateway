"""agents.slug — DNS-safe hostname label for per-agent ingress

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("slug", sa.String(63), nullable=True))
    # Existing agents get a stable, unique label derived from their id; new
    # ones are named after the agent (see agent_service._new_slug).
    op.execute("UPDATE agents SET slug = 'agent-' || substr(md5(id), 1, 12) WHERE slug IS NULL")
    op.alter_column("agents", "slug", nullable=False)
    op.create_index("ix_agents_slug", "agents", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agents_slug", table_name="agents")
    op.drop_column("agents", "slug")
