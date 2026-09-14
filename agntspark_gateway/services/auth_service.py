"""Registration and login business logic."""

from __future__ import annotations

from agntspark_core.auth import Role
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..exceptions import (
    AuthenticationError,
    EmailAlreadyRegisteredError,
    InvalidInviteError,
    RegistrationClosedError,
)
from ..models.user import User
from ..security.passwords import hash_password, verify_password
from .invite_service import claim_invite


async def register_user(
    db: AsyncSession, *, email: str, password: str, name: str, invite_code: str | None = None
) -> User:
    mode = settings.registration_mode
    if mode == "closed":
        raise RegistrationClosedError()

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise EmailAlreadyRegisteredError(email)

    if mode == "invite":
        if not invite_code or not invite_code.strip():
            raise InvalidInviteError()
        await claim_invite(db, invite_code)

    user = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        role=int(Role.VIEWER),
    )
    db.add(user)
    # Commits the invite's use together with the account.
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    # Deliberately generic message on both branches — avoids leaking whether
    # an email is registered (no user-enumeration via error text).
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AuthenticationError("Invalid email or password.")
    return user


__all__ = ["register_user", "authenticate_user"]
