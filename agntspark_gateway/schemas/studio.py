"""Schemas for /v1/studio* (builders) and /v1/public/assistants* (visitors)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..studio.templates import TEMPLATES

MAX_MESSAGE_CHARS = 4000
MAX_DOCUMENT_CHARS = 200_000


class TemplateOut(BaseModel):
    key: str
    name: str
    description: str
    default_greeting: str
    instructions_hint: str


class AssistantCreate(BaseModel):
    template: str
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=8000)
    # None = the template's default greeting.
    greeting: str | None = Field(default=None, max_length=500)

    @field_validator("template")
    @classmethod
    def _known_template(cls, v: str) -> str:
        if v not in TEMPLATES:
            raise ValueError(f"unknown template; choose one of {sorted(TEMPLATES)}")
        return v

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name can't be blank")
        return v


class AssistantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    instructions: str | None = Field(default=None, max_length=8000)
    greeting: str | None = Field(default=None, max_length=500)
    is_public: bool | None = None


class AssistantOut(BaseModel):
    id: str
    slug: str
    name: str
    template: str
    instructions: str
    greeting: str
    is_public: bool
    documents: int
    knowledge_chars: int
    created_at: datetime
    updated_at: datetime


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    chars: int
    created_at: datetime


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # Omit to start a conversation; send the returned id back to continue it.
    conversation_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message can't be blank")
        return v


class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ChatOut(BaseModel):
    conversation_id: uuid.UUID
    reply: MessageOut


class ConversationSummary(BaseModel):
    id: uuid.UUID
    source: Literal["test", "public"]
    messages: int
    preview: str
    updated_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    source: Literal["test", "public"]
    messages: list[MessageOut]


class StudioUsageOut(BaseModel):
    plan: str
    period_start: datetime
    messages_used: int
    messages_limit: int
    assistants: int
    assistants_limit: int
    knowledge_chars: int
    knowledge_chars_limit: int
    # False until the server has a model key; the app says chat is unavailable.
    chat_available: bool


class PublicAssistantOut(BaseModel):
    name: str
    greeting: str


class PublicConversationOut(BaseModel):
    id: uuid.UUID
    messages: list[MessageOut]
