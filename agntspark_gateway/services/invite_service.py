"""Invite codes for invite-only registration."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import InvalidInviteError, InviteNotFoundError
from ..models.invite import Invite

INVITE_PREFIX = "inv_"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def create_invite(
    db: AsyncSession,
    *,
    created_by: uuid.UUID,
    note: str,
    max_uses: int,
    expires_in_days: int | None,
) -> tuple[Invite, str]:
    """Returns ``(invite, raw_code)``; the raw code is never stored."""
    raw_code = f"{INVITE_PREFIX}{secrets.token_urlsafe(18)}"
    invite = Invite(
        code_hash=_hash_code(raw_code),
        code_prefix=raw_code[:10],
        note=note,
        created_by=created_by,
        max_uses=max_uses,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite, raw_code


async def list_invites(db: AsyncSession) -> list[Invite]:
    result = await db.execute(select(Invite).order_by(Invite.created_at.desc()))
    return list(result.scalars().all())


async def revoke_invite(db: AsyncSession, *, invite_id: uuid.UUID) -> None:
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise InviteNotFoundError(str(invite_id))
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        await db.commit()


async def claim_invite(db: AsyncSession, code: str) -> Invite:
    """Lock a usable invite and count one use.

    Does not commit: the caller commits together with the new account, so a
    registration that fails afterwards doesn't burn a use, and the row lock
    stops two sign-ups from both taking an invite's last use.
    """
    result = await db.execute(
        select(Invite).where(Invite.code_hash == _hash_code(code.strip())).with_for_update()
    )
    invite = result.scalar_one_or_none()
    if invite is None or invite.status != "active":
        raise InvalidInviteError()
    invite.use_count += 1
    return invite


__all__ = ["INVITE_PREFIX", "create_invite", "list_invites", "revoke_invite", "claim_invite"]
