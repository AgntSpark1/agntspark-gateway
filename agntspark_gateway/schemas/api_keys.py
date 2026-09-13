"""Schemas for /v1/api-keys*."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer


class ApiKeyCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=list)


class ApiKeyOut(BaseModel):
    """Console-compatible shape: {id, label, keyPreview, createdAt, lastUsedAt?, scopes}."""

    id: uuid.UUID
    label: str
    keyPreview: str
    createdAt: datetime
    lastUsedAt: datetime | None = None
    scopes: list[str]

    @field_serializer("id")
    def _serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class ApiKeyCreateResponse(ApiKeyOut):
    """Same as ApiKeyOut, plus the raw key — shown exactly once, at creation."""

    key: str
