"""enable pgvector extension

No table uses a vector column yet — this just makes the extension
available so a future embedding-backed feature (agent memory/RAG) doesn't
need its own migration + a database restart to add it. Requires the
pgvector/pgvector Postgres image (see docker-compose.yml); a plain
postgres:16 without the extension binary installed will fail this
migration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
