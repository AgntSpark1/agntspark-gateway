"""API key management business logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import ApiKeyNotFoundError
from ..models.api_key import ApiKey
from ..models.user import User
from ..security.api_keys import mint_api_key


async def create_api_key(
    db: AsyncSession, *, user: User, label: str, scopes: list[str]
) -> tuple[ApiKey, str]:
    """Returns (ApiKey row, raw_key). The raw key is never persisted."""
    raw_key, key_hash, key_prefix = mint_api_key(str(user.id), user.role_enum)

    api_key = ApiKey(
        user_id=user.id,
        label=label,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=scopes,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key, raw_key


async def list_api_keys(db: AsyncSession, *, user_id: uuid.UUID) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(db: AsyncSession, *, user_id: uuid.UUID, key_id: uuid.UUID) -> None:
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id))
    api_key = result.scalar_one_or_none()
    if api_key is None or not api_key.is_active:
        raise ApiKeyNotFoundError(str(key_id))

    api_key.revoked_at = datetime.now(UTC)
    await db.commit()


__all__ = ["create_api_key", "list_api_keys", "revoke_api_key"]
