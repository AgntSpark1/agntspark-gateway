"""Outgoing email over SMTP.

Works with any SMTP provider (Resend, Postmark, SES, Mailgun, ...). With
``smtp_host`` unset nothing is sent: the message is logged without its body,
which may hold a secret link.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import structlog

from ..config import settings

log = structlog.get_logger(__name__)


def email_enabled() -> bool:
    return bool(settings.smtp_host)


def _send_sync(message: EmailMessage) -> None:
    assert settings.smtp_host is not None
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_email(*, to: str, subject: str, text: str) -> None:
    """Send a plain-text email. Raises on SMTP failure."""
    if not email_enabled():
        log.warning("email: SMTP isn't configured, not sending", subject=subject)
        return
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    await asyncio.to_thread(_send_sync, message)


__all__ = ["email_enabled", "send_email"]
