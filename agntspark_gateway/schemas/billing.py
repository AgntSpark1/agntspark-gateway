"""Schemas for /v1/billing*."""

from __future__ import annotations

from pydantic import BaseModel


class BillingStatus(BaseModel):
    # False when Stripe isn't configured on this server.
    enabled: bool
    plan: str
    subscription_status: str | None
    # Whether a Stripe customer exists, i.e. the billing portal can be opened.
    has_billing_account: bool


class RedirectOut(BaseModel):
    url: str
