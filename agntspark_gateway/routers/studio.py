"""/v1/studio* — what a builder does in the app: assistants, knowledge, test chat."""

from __future__ import annotations

import uuid

from agntspark_core.auth import Role
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models.studio import ChatMessage
from ..schemas.studio import (
    AssistantCreate,
    AssistantOut,
    AssistantUpdate,
    ChatIn,
    ChatOut,
    ConversationOut,
    ConversationSummary,
    DocumentCreate,
    DocumentOut,
    MessageOut,
    StudioUsageOut,
    TemplateOut,
)
from ..security.dependencies import Principal, get_current_principal, require_role
from ..services import studio_service
from ..studio.llm import ChatModel, get_chat_model
from ..studio.templates import TEMPLATES

router = APIRouter(prefix="/v1/studio", tags=["studio"])

require_builder = require_role(Role.OPERATOR)


def message_out(m: ChatMessage) -> MessageOut:
    return MessageOut(role=m.role, content=m.content, created_at=m.created_at)  # type: ignore[arg-type]


@router.get("/templates", response_model=list[TemplateOut])
async def templates() -> list[TemplateOut]:
    return [
        TemplateOut(
            key=t.key,
            name=t.name,
            description=t.description,
            default_greeting=t.default_greeting,
            instructions_hint=t.instructions_hint,
        )
        for t in TEMPLATES.values()
    ]


@router.get("/usage", response_model=StudioUsageOut)
async def usage(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> StudioUsageOut:
    return await studio_service.usage(
        db,
        user_id=principal.user_id,
        chat_available=bool(settings.studio_anthropic_api_key),
    )


@router.get("/assistants", response_model=list[AssistantOut])
async def list_assistants(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> list[AssistantOut]:
    assistants = await studio_service.list_assistants(db, user_id=principal.user_id)
    return [await studio_service.to_out(db, a) for a in assistants]


@router.post("/assistants", response_model=AssistantOut, status_code=201)
async def create_assistant(
    body: AssistantCreate,
    principal: Principal = Depends(require_builder),
    db: AsyncSession = Depends(get_db),
) -> AssistantOut:
    assistant = await studio_service.create_assistant(db, user_id=principal.user_id, body=body)
    return await studio_service.to_out(db, assistant)


@router.get("/assistants/{assistant_id}", response_model=AssistantOut)
async def get_assistant(
    assistant_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> AssistantOut:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    return await studio_service.to_out(db, assistant)


@router.patch("/assistants/{assistant_id}", response_model=AssistantOut)
async def update_assistant(
    assistant_id: str,
    body: AssistantUpdate,
    principal: Principal = Depends(require_builder),
    db: AsyncSession = Depends(get_db),
) -> AssistantOut:
    assistant = await studio_service.update_assistant(
        db, user_id=principal.user_id, assistant_id=assistant_id, body=body
    )
    return await studio_service.to_out(db, assistant)


@router.delete("/assistants/{assistant_id}", status_code=204)
async def delete_assistant(
    assistant_id: str,
    principal: Principal = Depends(require_builder),
    db: AsyncSession = Depends(get_db),
) -> None:
    await studio_service.delete_assistant(db, user_id=principal.user_id, assistant_id=assistant_id)


@router.get("/assistants/{assistant_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    assistant_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentOut]:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    docs = await studio_service.list_documents(db, assistant=assistant)
    return [
        DocumentOut(id=d.id, title=d.title, chars=d.chars, created_at=d.created_at) for d in docs
    ]


@router.post("/assistants/{assistant_id}/documents", response_model=DocumentOut, status_code=201)
async def add_document(
    assistant_id: str,
    body: DocumentCreate,
    principal: Principal = Depends(require_builder),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    d = await studio_service.add_document(
        db, assistant=assistant, title=body.title, content=body.content
    )
    return DocumentOut(id=d.id, title=d.title, chars=d.chars, created_at=d.created_at)


@router.delete("/assistants/{assistant_id}/documents/{document_id}", status_code=204)
async def delete_document(
    assistant_id: str,
    document_id: uuid.UUID,
    principal: Principal = Depends(require_builder),
    db: AsyncSession = Depends(get_db),
) -> None:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    await studio_service.delete_document(db, assistant=assistant, document_id=document_id)


@router.post("/assistants/{assistant_id}/chat", response_model=ChatOut)
async def test_chat(
    assistant_id: str,
    body: ChatIn,
    principal: Principal = Depends(require_builder),
    db: AsyncSession = Depends(get_db),
    model: ChatModel = Depends(get_chat_model),
) -> ChatOut:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    conversation, answer = await studio_service.chat(
        db,
        assistant=assistant,
        model=model,
        message=body.message,
        conversation_id=body.conversation_id,
        source="test",
    )
    return ChatOut(conversation_id=conversation.id, reply=message_out(answer))


@router.get("/assistants/{assistant_id}/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    assistant_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    rows = await studio_service.list_conversations(db, assistant=assistant, limit=limit)
    return [
        ConversationSummary(
            id=c.id,
            source=c.source,  # type: ignore[arg-type]
            messages=n,
            preview=preview,
            updated_at=c.updated_at,
        )
        for c, n, preview in rows
    ]


@router.get(
    "/assistants/{assistant_id}/conversations/{conversation_id}", response_model=ConversationOut
)
async def get_conversation(
    assistant_id: str,
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    assistant = await studio_service.get_owned(
        db, user_id=principal.user_id, assistant_id=assistant_id
    )
    conversation, messages = await studio_service.get_conversation(
        db, assistant=assistant, conversation_id=conversation_id
    )
    return ConversationOut(
        id=conversation.id,
        source=conversation.source,  # type: ignore[arg-type]
        messages=[message_out(m) for m in messages],
    )
