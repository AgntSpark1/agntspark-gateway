"""Forgotten-password reset by email, and changing a known password.

Both stamp ``users.password_changed_at``, which invalidates every session
issued before it (see security/dependencies.py). API keys keep working.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..exceptions import InvalidResetTokenError, WrongPasswordError
from ..models.password_reset_token import PasswordResetToken
from ..models.user import User
from ..security.passwords import hash_password, verify_password
from . import email_service

log = structlog.get_logger(__name__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _reset_email(user: User, link: str) -> str:
    minutes = settings.password_reset_ttl_minutes
    return (
        f"Hi {user.name},\n\n"
        "Someone asked to reset the password for your AgntSpark account. "
        f"To choose a new one, open this link within {minutes} minutes:\n\n"
        f"{link}\n\n"
        "If it wasn't you, ignore this email; your password stays the same.\n\n"
        "AgntSpark\n"
    )


async def request_reset(db: AsyncSession, *, email: str) -> None:
    """Email a reset link if ``email`` belongs to an active account.

    Says nothing either way (no account enumeration), and an email that fails
    to send is logged rather than reported to the caller for the same reason.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return

    token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash(token),
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    await db.commit()

    link = f"{settings.public_base_url.rstrip('/')}/reset-password?token={token}"
    try:
        await email_service.send_email(
            to=user.email, subject="Reset your AgntSpark password", text=_reset_email(user, link)
        )
    except Exception as exc:
        log.error("password reset: sending the email failed", user_id=str(user.id), error=str(exc))


async def _set_password(db: AsyncSession, user: User, password: str) -> None:
    now = datetime.now(UTC)
    user.password_hash = hash_password(password)
    user.password_changed_at = now
    # Any other outstanding link for this account stops working too.
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    await db.commit()


async def confirm_reset(db: AsyncSession, *, token: str, password: str) -> User:
    result = await db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == _hash(token))
        .with_for_update()
    )
    record = result.scalar_one_or_none()
    if record is None or record.used_at is not None or record.expires_at <= datetime.now(UTC):
        raise InvalidResetTokenError()
    user = await db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise InvalidResetTokenError()
    await _set_password(db, user, password)
    log.info("password reset", user_id=str(user.id))
    return user


async def change_password(
    db: AsyncSession, *, user: User, current_password: str, new_password: str
) -> User:
    if not verify_password(current_password, user.password_hash):
        raise WrongPasswordError()
    await _set_password(db, user, new_password)
    log.info("password changed", user_id=str(user.id))
    return user


__all__ = ["request_reset", "confirm_reset", "change_password"]
