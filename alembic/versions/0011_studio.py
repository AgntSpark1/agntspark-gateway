"""studio: no-code assistants, knowledge, conversations, monthly reply usage

Revision ID: 0011
Revises: 0009
Create Date: 2026-10-05

Numbered 0011 because 0010 (password reset) is in flight on another branch.
Whichever of the two merges second must point its down_revision at the other.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "studio_assistants",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("template", sa.String(32), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("greeting", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index("ix_studio_assistants_user_id", "studio_assistants", ["user_id"])
    op.create_index("ix_studio_assistants_slug", "studio_assistants", ["slug"], unique=True)

    op.create_table(
        "studio_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assistant_id",
            sa.String(32),
            sa.ForeignKey("studio_assistants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_studio_documents_assistant_id", "studio_documents", ["assistant_id"])

    op.create_table(
        "studio_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studio_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assistant_id", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_studio_chunks_document_id", "studio_chunks", ["document_id"])
    op.create_index("ix_studio_chunks_assistant_id", "studio_chunks", ["assistant_id"])
    # Matches the expression knowledge.relevant_passages searches on.
    op.execute(
        "CREATE INDEX ix_studio_chunks_fts ON studio_chunks "
        "USING gin (to_tsvector('simple', content))"
    )

    op.create_table(
        "studio_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assistant_id",
            sa.String(32),
            sa.ForeignKey("studio_assistants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "ix_studio_conversations_assistant_id", "studio_conversations", ["assistant_id"]
    )

    op.create_table(
        "studio_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studio_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_studio_messages_conversation_id", "studio_messages", ["conversation_id"]
    )

    op.create_table(
        "studio_usage_months",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "month_start", name="uq_studio_usage_user_month"),
    )
    op.create_index("ix_studio_usage_months_user_id", "studio_usage_months", ["user_id"])


def downgrade() -> None:
    op.drop_table("studio_usage_months")
    op.drop_table("studio_messages")
    op.drop_table("studio_conversations")
    op.drop_table("studio_chunks")
    op.drop_table("studio_documents")
    op.drop_table("studio_assistants")
