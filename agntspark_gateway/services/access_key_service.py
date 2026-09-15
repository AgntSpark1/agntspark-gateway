"""Access keys that let callers reach a private agent's public URL.

Callers of the owner's agents ownership-check the agent first; nothing here
looks at who is asking.
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import AccessKeyNotFoundError
from ..models.agent import Agent
from ..models.agent_access_key import AgentAccessKey
from ..security.api_keys import hash_key

KEY_PREFIX = "agk_"


async def create_access_key(
    db: AsyncSession, *, agent: Agent, label: str
) -> tuple[AgentAccessKey, str]:
    """Returns the stored key and the raw key, which is never stored."""
    raw_key = KEY_PREFIX + secrets.token_urlsafe(32)
    key = AgentAccessKey(
        agent_id=agent.id, label=label, key_prefix=raw_key[:12], key_hash=hash_key(raw_key)
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key, raw_key


async def list_access_keys(db: AsyncSession, *, agent_id: str) -> list[AgentAccessKey]:
    result = await db.execute(
        select(AgentAccessKey)
        .where(AgentAccessKey.agent_id == agent_id)
        .order_by(AgentAccessKey.created_at)
    )
    return list(result.scalars().all())


async def delete_access_key(db: AsyncSession, *, agent_id: str, key_id: uuid.UUID) -> None:
    key = await db.get(AgentAccessKey, key_id)
    if key is None or key.agent_id != agent_id:
        raise AccessKeyNotFoundError(str(key_id))
    await db.delete(key)
    await db.commit()


async def is_valid_access_key(db: AsyncSession, *, agent_id: str, raw_key: str) -> bool:
    if not raw_key.startswith(KEY_PREFIX):
        return False
    result = await db.execute(
        select(AgentAccessKey.id).where(
            AgentAccessKey.agent_id == agent_id, AgentAccessKey.key_hash == hash_key(raw_key)
        )
    )
    return result.first() is not None


__all__ = [
    "KEY_PREFIX",
    "create_access_key",
    "delete_access_key",
    "is_valid_access_key",
    "list_access_keys",
]
