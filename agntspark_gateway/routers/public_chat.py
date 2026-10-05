"""/v1/public/assistants/<slug>* — a published assistant's chat page, no login.

Visitors are anonymous, so every message is rate limited per visitor IP, and
every reply counts against the owner's monthly allowance.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..exceptions import RateLimitExceededError
from ..schemas.studio import ChatIn, ChatOut, PublicAssistantOut, PublicConversationOut
from ..security.ingress_limits import FixedWindowLimiter
from ..services import studio_service
from ..studio.llm import ChatModel, get_chat_model
from .studio import message_out

router = APIRouter(prefix="/v1/public/assistants", tags=["public"])

# Per (assistant, visitor IP), in process like security/ingress_limits.py.
per_visitor = FixedWindowLimiter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/{slug}", response_model=PublicAssistantOut)
async def get_assistant(slug: str, db: AsyncSession = Depends(get_db)) -> PublicAssistantOut:
    assistant = await studio_service.get_public(db, slug=slug)
    return PublicAssistantOut(name=assistant.name, greeting=assistant.greeting)


@router.post("/{slug}/chat", response_model=ChatOut)
async def chat(
    slug: str,
    body: ChatIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    model: ChatModel = Depends(get_chat_model),
) -> ChatOut:
    assistant = await studio_service.get_public(db, slug=slug)
    retry_after = per_visitor.hit(
        f"{assistant.id}:{_client_ip(request)}", settings.studio_public_rpm_per_ip
    )
    if retry_after is not None:
        raise RateLimitExceededError(retry_after=retry_after)
    conversation, answer = await studio_service.chat(
        db,
        assistant=assistant,
        model=model,
        message=body.message,
        conversation_id=body.conversation_id,
        source="public",
    )
    return ChatOut(conversation_id=conversation.id, reply=message_out(answer))


@router.get("/{slug}/conversations/{conversation_id}", response_model=PublicConversationOut)
async def get_conversation(
    slug: str, conversation_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> PublicConversationOut:
    """The visitor's own conversation, so a page reload keeps it.

    The random conversation id is the visitor's only credential.
    """
    assistant = await studio_service.get_public(db, slug=slug)
    conversation, messages = await studio_service.get_conversation(
        db, assistant=assistant, conversation_id=conversation_id, source="public"
    )
    return PublicConversationOut(id=conversation.id, messages=[message_out(m) for m in messages])
