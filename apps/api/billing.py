"""Billing: subscriptions through a provider, Stripe for now.

The subscription logic (`routers/billing.py`, `parallx_db.accounts`) talks to
`BillingProvider`; `StripeProvider` is the one implementation. Changing
provider is a new implementation of this interface, not a rewrite.

What reaches the provider is decided on the server: the plan and interval a
person picked are checked against an allowlist and mapped to a Price ID from
the environment, never taken from the browser. Nothing a redirect says grants
a plan; only a webhook whose signature verifies does.

    STRIPE_SECRET_KEY                 sk_test_… / sk_live_…  (server only)
    STRIPE_WEBHOOK_SECRET             whsec_…
    STRIPE_PRICE_IIX_MONTHLY          Iix, $19 a month
    STRIPE_PRICE_IIX_ANNUAL           Iix, $190 a year
    STRIPE_PRICE_IIX_ANALYST_MONTHLY  IIx, $39 a month
    STRIPE_PRICE_IIX_ANALYST_ANNUAL   IIx, $390 a year
    BILLING_UNSUPPORTED_COUNTRIES     default "IN": no USD checkout there yet
    APP_URL                           where Checkout and the Portal return to
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

PAID_PLANS = ("investor", "analyst")
INTERVALS = ("month", "year")
PRICE_ENV = {
    ("investor", "month"): "STRIPE_PRICE_IIX_MONTHLY",
    ("investor", "year"): "STRIPE_PRICE_IIX_ANNUAL",
    ("analyst", "month"): "STRIPE_PRICE_IIX_ANALYST_MONTHLY",
    ("analyst", "year"): "STRIPE_PRICE_IIX_ANALYST_ANNUAL",
}


class BillingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def price_id(plan_id: str, interval: str) -> Optional[str]:
    env = PRICE_ENV.get((plan_id, interval))
    return (os.environ.get(env) or None) if env else None


def plan_for_price(price: Optional[str]) -> Optional[tuple[str, str]]:
    """(plan, interval) for a configured Price ID; None for any other."""
    for key, env in PRICE_ENV.items():
        if price and os.environ.get(env) == price:
            return key
    return None


def unsupported_countries() -> set[str]:
    raw = os.environ.get("BILLING_UNSUPPORTED_COUNTRIES", "IN")
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def app_url() -> str:
    return os.environ.get("APP_URL", "http://localhost:3000").rstrip("/")


@dataclass
class SubscriptionState:
    """A subscription as the provider says it is now."""
    subscription_id: str
    customer_id: str
    status: str
    price_id: Optional[str]
    interval: Optional[str]
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    cancel_at_period_end: bool = False
    canceled_at: Optional[datetime] = None
    #: who it is for, when the checkout said so
    user_id: Optional[str] = None


@dataclass
class WebhookEvent:
    id: str
    type: str
    created: Optional[datetime]
    object: dict = field(default_factory=dict)


@dataclass
class Checkout:
    id: str
    url: str


class BillingProvider(Protocol):
    name: str

    def create_customer(self, *, user_id: str, email: Optional[str], phone: Optional[str],
                        name: Optional[str]) -> str: ...

    def create_checkout(self, *, customer_id: str, price_id: str, user_id: str, plan_id: str,
                        interval: str, idempotency_key: str) -> Checkout: ...

    def change_plan(self, *, subscription_id: str, price_id: str,
                    idempotency_key: str) -> None: ...

    def cancel(self, *, subscription_id: str) -> None: ...

    def portal(self, *, customer_id: str) -> str: ...

    def refund(self, *, payment_intent_id: str, amount: Optional[int] = None) -> str: ...

    def parse_webhook(self, payload: bytes, signature: Optional[str]) -> WebhookEvent: ...

    def subscription(self, subscription_id: str) -> SubscriptionState: ...

    def subscription_state(self, obj: dict) -> SubscriptionState: ...

    def invoice_subscription(self, obj: dict) -> Optional[str]: ...


def _ts(v) -> Optional[datetime]:
    return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None


class StripeProvider:
    """Stripe Billing: Checkout Sessions, the Customer Portal, webhooks. The
    SDK is used on the server only; the secret key never leaves it."""

    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: Optional[str]) -> None:
        if not secret_key:
            raise BillingError("billing_not_configured", "STRIPE_SECRET_KEY is not set")
        self._key, self._webhook_secret = secret_key, webhook_secret

    def _s(self):
        import stripe
        return stripe

    def create_customer(self, *, user_id, email, phone, name) -> str:
        c = self._s().Customer.create(
            api_key=self._key, email=email or None, phone=phone or None, name=name or None,
            metadata={"user_id": str(user_id)}, idempotency_key=f"customer-{user_id}")
        return c["id"]

    def create_checkout(self, *, customer_id, price_id, user_id, plan_id, interval,
                        idempotency_key) -> Checkout:
        meta = {"user_id": str(user_id), "plan_id": plan_id, "interval": interval}
        s = self._s().checkout.Session.create(
            api_key=self._key, idempotency_key=idempotency_key, mode="subscription",
            customer=customer_id, client_reference_id=str(user_id),
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{app_url()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_url()}/pricing?checkout=canceled",
            billing_address_collection="required", metadata=meta,
            subscription_data={"metadata": meta})
        return Checkout(s["id"], s["url"])

    def change_plan(self, *, subscription_id, price_id, idempotency_key) -> None:
        stripe = self._s()
        sub = stripe.Subscription.retrieve(subscription_id, api_key=self._key)
        item = sub["items"]["data"][0]["id"]
        stripe.Subscription.modify(
            subscription_id, api_key=self._key, idempotency_key=idempotency_key,
            items=[{"id": item, "price": price_id}], proration_behavior="create_prorations",
            cancel_at_period_end=False)

    def cancel(self, *, subscription_id) -> None:
        self._s().Subscription.modify(subscription_id, api_key=self._key,
                                      cancel_at_period_end=True)

    def portal(self, *, customer_id) -> str:
        return self._s().billing_portal.Session.create(
            api_key=self._key, customer=customer_id,
            return_url=f"{app_url()}/account")["url"]

    def refund(self, *, payment_intent_id, amount=None) -> str:
        r = self._s().Refund.create(api_key=self._key, payment_intent=payment_intent_id,
                                    **({"amount": amount} if amount else {}))
        return r["id"]

    def parse_webhook(self, payload: bytes, signature) -> WebhookEvent:
        if not self._webhook_secret:
            raise BillingError("billing_not_configured", "STRIPE_WEBHOOK_SECRET is not set")
        stripe = self._s()
        try:
            stripe.WebhookSignature.verify_header(payload.decode("utf-8"), signature or "",
                                                  self._webhook_secret, tolerance=300)
        except (stripe.error.SignatureVerificationError, ValueError) as exc:
            raise BillingError("bad_signature", "webhook signature did not verify") from exc
        body = json.loads(payload)
        return WebhookEvent(body["id"], body["type"], _ts(body.get("created")),
                            body.get("data", {}).get("object", {}))

    def subscription(self, subscription_id) -> SubscriptionState:
        sub = self._s().Subscription.retrieve(subscription_id, api_key=self._key)
        return self.subscription_state(sub.to_dict() if hasattr(sub, "to_dict") else dict(sub))

    def subscription_state(self, obj: dict) -> SubscriptionState:
        items = (obj.get("items") or {}).get("data") or [{}]
        item = items[0]
        price = item.get("price") or {}
        # since API 2025-03-31 the period is on the item; before, on the subscription
        start = item.get("current_period_start") or obj.get("current_period_start")
        end = item.get("current_period_end") or obj.get("current_period_end")
        return SubscriptionState(
            subscription_id=obj["id"], customer_id=obj.get("customer"),
            status=obj.get("status"), price_id=price.get("id"),
            interval=(price.get("recurring") or {}).get("interval"),
            period_start=_ts(start), period_end=_ts(end),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
            canceled_at=_ts(obj.get("canceled_at")),
            user_id=(obj.get("metadata") or {}).get("user_id"))

    def invoice_subscription(self, obj: dict) -> Optional[str]:
        sub = obj.get("subscription")
        if not sub:
            sub = (((obj.get("parent") or {}).get("subscription_details") or {})
                   .get("subscription"))
        return sub.get("id") if isinstance(sub, dict) else sub


_provider = None


def get_billing() -> Optional[BillingProvider]:
    """The configured provider; None when billing is not configured."""
    global _provider
    if _provider is None and os.environ.get("STRIPE_SECRET_KEY"):
        _provider = StripeProvider(os.environ["STRIPE_SECRET_KEY"],
                                   os.environ.get("STRIPE_WEBHOOK_SECRET"))
    return _provider


def set_billing(p) -> None:
    global _provider
    _provider = p
