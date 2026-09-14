"""Schemas for /v1/admin/invites*."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class InviteCreateRequest(BaseModel):
    note: str = Field(default="", max_length=255)
    max_uses: int = Field(default=1, ge=1, le=1000)
    # None → never expires.
    expires_in_days: int | None = Field(default=14, ge=1, le=365)


class InviteOut(BaseModel):
    id: uuid.UUID
    code_prefix: str
    note: str
    max_uses: int
    use_count: int
    status: Literal["active", "used", "expired", "revoked"]
    expires_at: datetime | None
    created_at: datetime

    @field_serializer("id")
    def _serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class InviteCreateResponse(InviteOut):
    """InviteOut plus the raw code — shown exactly once, at creation."""

    code: str
