"""/v1/billing — plan status, Stripe Checkout, customer portal, and the Stripe webhook."""

from __future__ import annotations

import json

import stripe
from agntspark_core.auth import Role
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient

from ..config import settings
from ..db import get_db
from ..exceptions import AuthenticationError, BillingNotConfiguredError, InvalidWebhookError
from ..models.user import User
from ..schemas.billing import BillingStatus, RedirectOut
from ..security.dependencies import Principal, get_current_principal, require_role
from ..services import billing_service

router = APIRouter(prefix="/v1/billing", tags=["billing"])

# Changing what an account pays for takes a logged-in developer (or admin).
_require_billing_session = require_role(Role.OPERATOR, jwt_only=True)


def billing_enabled() -> bool:
    return bool(settings.stripe_secret_key and settings.stripe_price_pro)


def get_stripe_client() -> StripeClient:
    if not billing_enabled():
        raise BillingNotConfiguredError()
    return StripeClient(settings.stripe_secret_key)  # type: ignore[arg-type]  # checked above


async def _user(db: AsyncSession, principal: Principal) -> User:
    user = await db.get(User, principal.user_id)
    if user is None:
        raise AuthenticationError("Invalid or expired token.")
    return user


@router.get("", response_model=BillingStatus)
async def billing_status(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> BillingStatus:
    user = await _user(db, principal)
    return BillingStatus(
        enabled=billing_enabled(),
        plan=user.plan,
        subscription_status=user.subscription_status,
        has_billing_account=user.stripe_customer_id is not None,
    )


@router.post("/checkout", response_model=RedirectOut)
async def checkout(
    principal: Principal = Depends(_require_billing_session),
    db: AsyncSession = Depends(get_db),
    client: StripeClient = Depends(get_stripe_client),
) -> RedirectOut:
    user = await _user(db, principal)
    return RedirectOut(url=await billing_service.checkout_url(db, client, user=user))


@router.post("/portal", response_model=RedirectOut)
async def portal(
    principal: Principal = Depends(_require_billing_session),
    db: AsyncSession = Depends(get_db),
    client: StripeClient = Depends(get_stripe_client),
) -> RedirectOut:
    user = await _user(db, principal)
    return RedirectOut(url=await billing_service.portal_url(db, client, user=user))


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    if not settings.stripe_webhook_secret:
        raise BillingNotConfiguredError()
    payload = await request.body()
    try:
        stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise InvalidWebhookError() from exc
    # Verified above; parse the raw JSON rather than depend on StripeObject's shape.
    await billing_service.handle_event(db, json.loads(payload))
    return {"received": True}
