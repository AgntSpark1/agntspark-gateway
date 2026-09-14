"""users.plan; accounts default to the developer role

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("plan", sa.String(32), nullable=False, server_default="free"))
    # Roles are enforced from this revision on and new accounts are
    # developers. Existing viewers could already deploy (the role was never
    # checked), so they keep that ability. Not reverted on downgrade.
    op.execute("UPDATE users SET role = 1 WHERE role = 0")


def downgrade() -> None:
    op.drop_column("users", "plan")
