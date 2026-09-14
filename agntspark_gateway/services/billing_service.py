"""Stripe subscriptions: Checkout to upgrade, the customer portal to manage,
webhooks to keep ``users.plan`` in sync.

Stripe decides whether someone is paying; ``users.plan`` follows the
subscription's status through webhooks. What a plan allows lives in plans.py.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient

from ..config import settings
from ..exceptions import BillingConflictError
from ..models.user import User

log = structlog.get_logger(__name__)

PAID_PLAN = "pro"
# past_due keeps access while Stripe retries the payment; anything else
# (canceled, unpaid, incomplete, incomplete_expired, paused) drops to free.
PAID_STATUSES = frozenset({"active", "trialing", "past_due"})


def plan_for_status(status: str | None) -> str:
    return PAID_PLAN if status in PAID_STATUSES else "free"


def _console_url(path: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}{path}"


async def _ensure_customer(db: AsyncSession, client: StripeClient, user: User) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = await asyncio.to_thread(
        client.v1.customers.create,
        {"email": user.email, "name": user.name, "metadata": {"user_id": str(user.id)}},
    )
    user.stripe_customer_id = customer.id
    await db.commit()
    return str(customer.id)


async def checkout_url(db: AsyncSession, client: StripeClient, *, user: User) -> str:
    if user.subscription_status in PAID_STATUSES:
        raise BillingConflictError(
            "You already have an active subscription; manage it from the billing portal.",
            code="ALREADY_SUBSCRIBED",
        )
    customer_id = await _ensure_customer(db, client, user)
    session = await asyncio.to_thread(
        client.v1.checkout.sessions.create,
        {
            "mode": "subscription",
            "customer": customer_id,
            "client_reference_id": str(user.id),
            "line_items": [{"price": settings.stripe_price_pro, "quantity": 1}],
            "subscription_data": {"metadata": {"user_id": str(user.id)}},
            "allow_promotion_codes": True,
            "success_url": _console_url("/settings?billing=success"),
            "cancel_url": _console_url("/settings?billing=cancelled"),
        },
    )
    return str(session.url)


async def portal_url(db: AsyncSession, client: StripeClient, *, user: User) -> str:
    if not user.stripe_customer_id:
        raise BillingConflictError(
            "There's no billing account yet — upgrade first.", code="NO_BILLING_ACCOUNT"
        )
    session = await asyncio.to_thread(
        client.v1.billing_portal.sessions.create,
        {
            "customer": user.stripe_customer_id,
            "return_url": _console_url("/settings?billing=portal"),
        },
    )
    return str(session.url)


async def _find_user(
    db: AsyncSession, *, user_id: str | None, customer_id: str | None
) -> User | None:
    if user_id:
        try:
            user = await db.get(User, uuid.UUID(user_id))
        except ValueError:
            user = None
        if user is not None:
            return user
    if customer_id:
        result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
        return result.scalar_one_or_none()
    return None


async def handle_event(db: AsyncSession, event: dict[str, Any]) -> None:
    """Apply a verified Stripe event. Unknown events and customers are ignored."""
    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        user = await _find_user(
            db, user_id=obj.get("client_reference_id"), customer_id=obj.get("customer")
        )
        if user is None or obj.get("mode") != "subscription":
            return
        user.stripe_customer_id = obj.get("customer") or user.stripe_customer_id
        user.stripe_subscription_id = obj.get("subscription") or user.stripe_subscription_id
        if obj.get("payment_status") in ("paid", "no_payment_required"):
            # The subscription events carry the authoritative status, but
            # granting access here avoids a gap if they arrive late.
            user.subscription_status = "active"
            user.plan = PAID_PLAN
        await db.commit()
        return

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        user = await _find_user(
            db,
            user_id=(obj.get("metadata") or {}).get("user_id"),
            customer_id=obj.get("customer"),
        )
        if user is None:
            log.warning("billing: event for unknown customer", event_type=event_type)
            return
        subscription_id = obj.get("id")
        if (
            event_type == "customer.subscription.deleted"
            and user.stripe_subscription_id
            and subscription_id != user.stripe_subscription_id
        ):
            # An old subscription ending doesn't affect the current one.
            return
        status = "canceled" if event_type == "customer.subscription.deleted" else obj.get("status")
        user.stripe_customer_id = obj.get("customer") or user.stripe_customer_id
        user.stripe_subscription_id = subscription_id
        user.subscription_status = status
        user.plan = plan_for_status(status)
        await db.commit()
        log.info(
            "billing: subscription synced", user_id=str(user.id), status=status, plan=user.plan
        )


__all__ = [
    "PAID_PLAN",
    "PAID_STATUSES",
    "plan_for_status",
    "checkout_url",
    "portal_url",
    "handle_event",
]
