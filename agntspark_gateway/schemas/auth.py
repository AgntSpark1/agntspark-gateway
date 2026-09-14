"""Schemas for /v1/auth/*."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_serializer


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1, max_length=255)
    # Required when registration_mode is "invite"; ignored otherwise.
    invite_code: str | None = Field(default=None, max_length=128)


class RegistrationInfo(BaseModel):
    mode: Literal["open", "invite", "closed"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    """Console-compatible shape: {id, name, email, role, avatarUrl}."""

    id: uuid.UUID
    name: str
    email: str
    role: str
    plan: str = "free"
    avatarUrl: str | None = None

    @field_serializer("id")
    def _serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
