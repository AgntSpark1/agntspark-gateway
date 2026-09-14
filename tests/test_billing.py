"""Stripe billing: Checkout, portal, and webhook-driven plan changes.

Stripe's API is replaced by a fake client; webhook payloads are signed with
Stripe's real scheme so signature verification is exercised for real.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from agntspark_core.auth import Role
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway.config import settings
from agntspark_gateway.models.user import User
from agntspark_gateway.routers import billing as billing_router
from agntspark_gateway.security.passwords import hash_password

pytestmark = pytest.mark.integration

PASSWORD = "hunter2hunter2"
WEBHOOK_SECRET = "whsec_test_secret"


class FakeStripe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.v1 = SimpleNamespace(
            customers=SimpleNamespace(
                create=self._record("customer", SimpleNamespace(id="cus_test123"))
            ),
            checkout=SimpleNamespace(
                sessions=SimpleNamespace(
                    create=self._record(
                        "checkout", SimpleNamespace(url="https://checkout.stripe.test/s")
                    )
                )
            ),
            billing_portal=SimpleNamespace(
                sessions=SimpleNamespace(
                    create=self._record(
                        "portal", SimpleNamespace(url="https://billing.stripe.test/p")
                    )
                )
            ),
        )

    def _record(self, name: str, result: Any) -> Any:
        def call(params: dict[str, Any] | None = None, options: Any = None) -> Any:
            self.calls.append((name, params or {}))
            return result

        return call

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "public_base_url", "https://console.test/")
    monkeypatch.setattr(billing_router, "StripeClient", lambda key: fake)
    return fake


async def _login(
    client: AsyncClient, db: AsyncSession, email: str, role: Role = Role.OPERATOR
) -> dict[str, str]:
    db.add(User(email=email, password_hash=hash_password(PASSWORD), name="Payer", role=int(role)))
    await db.commit()
    resp = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _post_event(
    client: AsyncClient, event: dict[str, Any], secret: str = WEBHOOK_SECRET
) -> Any:
    payload = json.dumps(event).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={
            "Stripe-Signature": f"t={timestamp},v1={signature}",
            "Content-Type": "application/json",
        },
    )


def _subscription_event(
    kind: str, status: str, user_id: str, sub_id: str = "sub_1"
) -> dict[str, Any]:
    return {
        "type": f"customer.subscription.{kind}",
        "data": {
            "object": {
                "id": sub_id,
                "customer": "cus_test123",
                "status": status,
                "metadata": {"user_id": user_id},
            }
        },
    }


async def test_disabled_without_stripe_config(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _login(client, db_session, "off@agntspark.com")

    status = (await client.get("/v1/billing", headers=headers)).json()
    checkout = await client.post("/v1/billing/checkout", headers=headers)

    assert status == {
        "enabled": False,
        "plan": "free",
        "subscription_status": None,
        "has_billing_account": False,
    }
    assert checkout.status_code == 503
    assert checkout.json()["code"] == "BILLING_NOT_CONFIGURED"


async def test_checkout_creates_the_customer_once(
    client: AsyncClient, db_session: AsyncSession, fake_stripe: FakeStripe
) -> None:
    headers = await _login(client, db_session, "buyer@agntspark.com")

    first = await client.post("/v1/billing/checkout", headers=headers)
    second = await client.post("/v1/billing/checkout", headers=headers)

    assert first.status_code == 200
    assert first.json() == {"url": "https://checkout.stripe.test/s"}
    assert second.status_code == 200
    assert fake_stripe.names() == ["customer", "checkout", "checkout"]
    params = fake_stripe.calls[1][1]
    assert params["mode"] == "subscription"
    assert params["customer"] == "cus_test123"
    assert params["line_items"] == [{"price": "price_pro_test", "quantity": 1}]
    assert params["success_url"] == "https://console.test/settings?billing=success"


async def test_portal_needs_a_billing_account(
    client: AsyncClient, db_session: AsyncSession, fake_stripe: FakeStripe
) -> None:
    headers = await _login(client, db_session, "portal@agntspark.com")

    before = await client.post("/v1/billing/portal", headers=headers)
    await client.post("/v1/billing/checkout", headers=headers)
    after = await client.post("/v1/billing/portal", headers=headers)

    assert before.status_code == 409
    assert before.json()["code"] == "NO_BILLING_ACCOUNT"
    assert after.json() == {"url": "https://billing.stripe.test/p"}


async def test_viewers_cannot_start_checkout(
    client: AsyncClient, db_session: AsyncSession, fake_stripe: FakeStripe
) -> None:
    headers = await _login(client, db_session, "viewer-pay@agntspark.com", role=Role.VIEWER)
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 403


async def test_webhook_rejects_bad_signatures(client: AsyncClient, fake_stripe: FakeStripe) -> None:
    resp = await _post_event(client, {"type": "ping", "data": {"object": {}}}, secret="whsec_wrong")
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_WEBHOOK_SIGNATURE"


async def test_subscription_lifecycle_drives_the_plan(
    client: AsyncClient, db_session: AsyncSession, fake_stripe: FakeStripe
) -> None:
    headers = await _login(client, db_session, "lifecycle@agntspark.com")
    user_id = (await client.get("/v1/auth/me", headers=headers)).json()["id"]
    await client.post("/v1/billing/checkout", headers=headers)

    async def plan() -> tuple[str, str | None]:
        body = (await client.get("/v1/billing", headers=headers)).json()
        return body["plan"], body["subscription_status"]

    assert (
        await _post_event(client, _subscription_event("created", "active", user_id))
    ).status_code == 200
    assert await plan() == ("pro", "active")

    await _post_event(client, _subscription_event("updated", "past_due", user_id))
    assert await plan() == ("pro", "past_due")

    # An old subscription ending doesn't downgrade the current one.
    await _post_event(client, _subscription_event("deleted", "canceled", user_id, sub_id="sub_old"))
    assert await plan() == ("pro", "past_due")

    await _post_event(client, _subscription_event("deleted", "canceled", user_id))
    assert await plan() == ("free", "canceled")

    again = await client.post("/v1/billing/checkout", headers=headers)
    assert again.status_code == 200


async def test_active_subscribers_are_sent_to_the_portal(
    client: AsyncClient, db_session: AsyncSession, fake_stripe: FakeStripe
) -> None:
    headers = await _login(client, db_session, "subscribed@agntspark.com")
    user_id = (await client.get("/v1/auth/me", headers=headers)).json()["id"]
    await client.post("/v1/billing/checkout", headers=headers)
    await _post_event(client, _subscription_event("created", "active", user_id))

    resp = await client.post("/v1/billing/checkout", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["code"] == "ALREADY_SUBSCRIBED"


async def test_events_for_unknown_customers_are_ignored(
    client: AsyncClient, fake_stripe: FakeStripe
) -> None:
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_x", "customer": "cus_nobody", "status": "active"}},
    }
    assert (await _post_event(client, event)).status_code == 200
