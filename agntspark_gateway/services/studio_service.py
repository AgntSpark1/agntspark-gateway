"""Studio assistants: CRUD, knowledge, chat, and the monthly reply allowance.

Like quota_service, allowance checks read usage and then act without a lock,
so two concurrent replies at the edge of the allowance can both go through.
"""

from __future__ import annotations

import re
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import (
    AssistantNotFoundError,
    AuthenticationError,
    ConversationNotFoundError,
    KnowledgeDocumentNotFoundError,
    QuotaExceededError,
)
from ..models.studio import (
    Assistant,
    ChatMessage,
    Conversation,
    KnowledgeChunk,
    KnowledgeDocument,
    StudioUsageMonth,
)
from ..models.user import User
from ..schemas.studio import AssistantCreate, AssistantOut, AssistantUpdate, StudioUsageOut
from ..studio import knowledge
from ..studio.limits import studio_limits_for
from ..studio.llm import ChatModel
from ..studio.prompting import build_system_prompt
from ..studio.templates import TEMPLATES
from . import quota_service
from .metering_service import month_start

# Turns of earlier conversation sent with each new message.
HISTORY_MESSAGES = 20

_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def _new_id() -> str:
    return f"ast_{secrets.token_urlsafe(15)}"


def _new_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "assistant"
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(6))
    return f"{base}-{suffix}"


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationError("Invalid or expired token.")
    return user


# ── usage ────────────────────────────────────────────────────────────────


async def _messages_this_month(db: AsyncSession, user_id: uuid.UUID, start: datetime) -> int:
    used = (
        await db.execute(
            select(StudioUsageMonth.messages).where(
                StudioUsageMonth.user_id == user_id, StudioUsageMonth.month_start == start
            )
        )
    ).scalar_one_or_none()
    return used or 0


async def _knowledge_chars(db: AsyncSession, user_id: uuid.UUID) -> int:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(KnowledgeDocument.chars), 0))
            .join(Assistant, Assistant.id == KnowledgeDocument.assistant_id)
            .where(Assistant.user_id == user_id)
        )
    ).scalar_one()
    return int(total)


async def _assistant_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(Assistant).where(Assistant.user_id == user_id)
        )
    ).scalar_one()


async def usage(db: AsyncSession, *, user_id: uuid.UUID, chat_available: bool) -> StudioUsageOut:
    user = await _user(db, user_id)
    limits = studio_limits_for(user.plan)
    start = month_start()
    return StudioUsageOut(
        plan=user.plan,
        period_start=start,
        messages_used=await _messages_this_month(db, user_id, start),
        messages_limit=limits.max_messages_month,
        assistants=await _assistant_count(db, user_id),
        assistants_limit=limits.max_assistants,
        knowledge_chars=await _knowledge_chars(db, user_id),
        knowledge_chars_limit=limits.max_knowledge_chars,
        chat_available=chat_available,
    )


async def _ensure_reply_allowed(db: AsyncSession, owner_id: uuid.UUID) -> None:
    owner = await _user(db, owner_id)
    if quota_service.is_exempt(owner):
        return
    limit = studio_limits_for(owner.plan).max_messages_month
    used = await _messages_this_month(db, owner_id, month_start())
    if used + 1 > limit:
        raise QuotaExceededError(
            "messages_month", plan=owner.plan, limit=limit, current=used, requested=1
        )


async def _record_reply(
    db: AsyncSession, owner_id: uuid.UUID, *, input_tokens: int, output_tokens: int
) -> None:
    stmt = insert(StudioUsageMonth).values(
        id=uuid.uuid4(),
        user_id=owner_id,
        month_start=month_start(),
        messages=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_studio_usage_user_month",
        set_={
            "messages": StudioUsageMonth.messages + 1,
            "input_tokens": StudioUsageMonth.input_tokens + input_tokens,
            "output_tokens": StudioUsageMonth.output_tokens + output_tokens,
        },
    )
    await db.execute(stmt)


# ── assistants ───────────────────────────────────────────────────────────


async def to_out(db: AsyncSession, assistant: Assistant) -> AssistantOut:
    docs, chars = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(KnowledgeDocument.chars), 0)).where(
                KnowledgeDocument.assistant_id == assistant.id
            )
        )
    ).one()
    return AssistantOut(
        id=assistant.id,
        slug=assistant.slug,
        name=assistant.name,
        template=assistant.template,
        instructions=assistant.instructions,
        greeting=assistant.greeting,
        is_public=assistant.is_public,
        documents=docs,
        knowledge_chars=int(chars),
        created_at=assistant.created_at,
        updated_at=assistant.updated_at,
    )


async def create_assistant(
    db: AsyncSession, *, user_id: uuid.UUID, body: AssistantCreate
) -> Assistant:
    user = await _user(db, user_id)
    if not quota_service.is_exempt(user):
        limit = studio_limits_for(user.plan).max_assistants
        count = await _assistant_count(db, user_id)
        if count + 1 > limit:
            raise QuotaExceededError(
                "assistants", plan=user.plan, limit=limit, current=count, requested=1
            )
    template = TEMPLATES[body.template]
    assistant = Assistant(
        id=_new_id(),
        user_id=user_id,
        slug=_new_slug(body.name),
        name=body.name,
        template=body.template,
        instructions=body.instructions,
        greeting=body.greeting if body.greeting is not None else template.default_greeting,
        is_public=False,
    )
    db.add(assistant)
    await db.commit()
    await db.refresh(assistant)
    return assistant


async def list_assistants(db: AsyncSession, *, user_id: uuid.UUID) -> list[Assistant]:
    rows = await db.execute(
        select(Assistant).where(Assistant.user_id == user_id).order_by(Assistant.created_at.desc())
    )
    return list(rows.scalars())


async def get_owned(db: AsyncSession, *, user_id: uuid.UUID, assistant_id: str) -> Assistant:
    assistant = await db.get(Assistant, assistant_id)
    if assistant is None or assistant.user_id != user_id:
        raise AssistantNotFoundError(assistant_id)
    return assistant


async def update_assistant(
    db: AsyncSession, *, user_id: uuid.UUID, assistant_id: str, body: AssistantUpdate
) -> Assistant:
    assistant = await get_owned(db, user_id=user_id, assistant_id=assistant_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip() or assistant.name
    for field, value in changes.items():
        setattr(assistant, field, value)
    await db.commit()
    await db.refresh(assistant)
    return assistant


async def delete_assistant(db: AsyncSession, *, user_id: uuid.UUID, assistant_id: str) -> None:
    assistant = await get_owned(db, user_id=user_id, assistant_id=assistant_id)
    # Chunks have no FK to the assistant (they cascade from their document).
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.assistant_id == assistant.id))
    await db.delete(assistant)
    await db.commit()


async def get_public(db: AsyncSession, *, slug: str) -> Assistant:
    assistant = (
        await db.execute(select(Assistant).where(Assistant.slug == slug))
    ).scalar_one_or_none()
    if assistant is None or not assistant.is_public:
        raise AssistantNotFoundError(slug)
    return assistant


# ── knowledge ────────────────────────────────────────────────────────────


async def list_documents(db: AsyncSession, *, assistant: Assistant) -> list[KnowledgeDocument]:
    rows = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.assistant_id == assistant.id)
        .order_by(KnowledgeDocument.created_at)
    )
    return list(rows.scalars())


async def add_document(
    db: AsyncSession, *, assistant: Assistant, title: str, content: str
) -> KnowledgeDocument:
    owner = await _user(db, assistant.user_id)
    if not quota_service.is_exempt(owner):
        limit = studio_limits_for(owner.plan).max_knowledge_chars
        current = await _knowledge_chars(db, owner.id)
        if current + len(content) > limit:
            raise QuotaExceededError(
                "knowledge_chars",
                plan=owner.plan,
                limit=limit,
                current=current,
                requested=len(content),
            )
    document = KnowledgeDocument(
        id=uuid.uuid4(), assistant_id=assistant.id, title=title.strip(), chars=len(content)
    )
    db.add(document)
    await db.flush()
    for position, passage in enumerate(knowledge.split_into_chunks(content)):
        db.add(
            KnowledgeChunk(
                document_id=document.id,
                assistant_id=assistant.id,
                position=position,
                content=passage,
            )
        )
    await db.commit()
    await db.refresh(document)
    return document


async def delete_document(
    db: AsyncSession, *, assistant: Assistant, document_id: uuid.UUID
) -> None:
    document = await db.get(KnowledgeDocument, document_id)
    if document is None or document.assistant_id != assistant.id:
        raise KnowledgeDocumentNotFoundError(str(document_id))
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
    await db.delete(document)
    await db.commit()


# ── conversations ────────────────────────────────────────────────────────


async def _conversation(
    db: AsyncSession, *, assistant: Assistant, conversation_id: uuid.UUID, source: str | None
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.assistant_id != assistant.id
        or (source is not None and conversation.source != source)
    ):
        raise ConversationNotFoundError(str(conversation_id))
    return conversation


async def messages_of(db: AsyncSession, conversation_id: uuid.UUID) -> list[ChatMessage]:
    rows = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return list(rows.scalars())


async def get_conversation(
    db: AsyncSession, *, assistant: Assistant, conversation_id: uuid.UUID, source: str | None = None
) -> tuple[Conversation, list[ChatMessage]]:
    conversation = await _conversation(
        db, assistant=assistant, conversation_id=conversation_id, source=source
    )
    return conversation, await messages_of(db, conversation.id)


async def list_conversations(
    db: AsyncSession, *, assistant: Assistant, limit: int = 50
) -> list[tuple[Conversation, int, str]]:
    """Newest first, each with its message count and first visitor message."""
    count = (
        select(func.count())
        .where(ChatMessage.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    first = (
        select(ChatMessage.content)
        .where(ChatMessage.conversation_id == Conversation.id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at)
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(Conversation, count, first)
        .where(Conversation.assistant_id == assistant.id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    return [(c, n, (p or "")[:140]) for c, n, p in rows]


async def chat(
    db: AsyncSession,
    *,
    assistant: Assistant,
    model: ChatModel,
    message: str,
    conversation_id: uuid.UUID | None,
    source: str,
) -> tuple[Conversation, ChatMessage]:
    """One visitor (or owner) message in, one assistant reply out.

    Nothing is stored or counted if the model fails, so a retry is free.
    """
    await _ensure_reply_allowed(db, assistant.user_id)

    if conversation_id is not None:
        conversation = await _conversation(
            db, assistant=assistant, conversation_id=conversation_id, source=source
        )
        history = (await messages_of(db, conversation.id))[-HISTORY_MESSAGES:]
    else:
        conversation = Conversation(id=uuid.uuid4(), assistant_id=assistant.id, source=source)
        history = []

    passages = await knowledge.relevant_passages(db, assistant_id=assistant.id, question=message)
    system = build_system_prompt(assistant, passages)
    turns = [{"role": m.role, "content": m.content} for m in history]
    turns.append({"role": "user", "content": message})
    # The first turn sent must be the visitor's; a cut history can start mid-pair.
    while turns and turns[0]["role"] != "user":
        turns.pop(0)

    reply = await model.reply(system=system, messages=turns)

    if conversation_id is None:
        db.add(conversation)
        await db.flush()
    else:
        conversation.updated_at = datetime.now(UTC)
    # Explicit times: now() is the transaction's start, the same for both rows.
    asked_at = datetime.now(UTC)
    db.add(
        ChatMessage(
            conversation_id=conversation.id, role="user", content=message, created_at=asked_at
        )
    )
    answer = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        created_at=asked_at + timedelta(microseconds=1),
        content=reply.text,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )
    db.add(answer)
    await _record_reply(
        db,
        assistant.user_id,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )
    await db.commit()
    await db.refresh(answer)
    await db.refresh(conversation)
    return conversation, answer
