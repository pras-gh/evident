"""Subscriptions: checkout, the provider's webhook, cancelling, the portal.

    POST /v1/billing/checkout   a plan and interval → Stripe Checkout (or a plan change)
    POST /v1/billing/webhook    the provider's events, signature-verified
    POST /v1/billing/cancel     cancel at the end of the period
    GET  /v1/billing/portal     the Customer Portal: card, invoices, plan, cancel

A paid plan is granted by the webhook alone. The success page polls
`GET /v1/me/subscription` until the webhook has run; a redirect from
Checkout proves nothing. Webhooks are applied once each (`billing_events`),
in any order: subscription events carry the provider's time, and an older one
than the last applied changes nothing; checkout and invoice events re-read the
subscription from the provider, which is current whenever they arrive.

India: a Stripe account cannot be assumed to charge Indian-issued cards in
USD, so checkout is refused for a billing country in
BILLING_UNSUPPORTED_COUNTRIES (default IN) until an INR flow, with its
recurring-payment compliance, is in place.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import IdempotencyKey, User
from parallx_db import accounts

from ..billing import (BillingError, get_billing, plan_for_price, price_id,
                       unsupported_countries)
from ..deps import get_db
from ..errors import ApiError
from ..schemas import BillingLinkOut, CancelOut, CheckoutIn, CheckoutOut
from ..viewer import Viewer, get_viewer

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger("parallx.billing")

HANDLED = frozenset({"checkout.session.completed", "customer.subscription.created",
                     "customer.subscription.updated", "customer.subscription.deleted",
                     "invoice.paid", "invoice.payment_failed",
                     "invoice.payment_action_required"})


def _provider():
    p = get_billing()
    if p is None:
        raise ApiError(503, "billing_not_configured",
                       "Payments aren't set up on this server yet.")
    return p


@router.post("/checkout", response_model=CheckoutOut, summary="Subscribe to a plan")
async def checkout(body: CheckoutIn, viewer: Viewer = Depends(get_viewer),
                   db: AsyncSession = Depends(get_db)) -> CheckoutOut:
    provider = _provider()
    price = price_id(body.plan_id, body.interval)
    if not price:
        raise ApiError(503, "billing_not_configured",
                       f"No price is configured for {body.plan_id} ({body.interval}).")
    if body.country.upper() in unsupported_countries():
        raise ApiError(409, "market_unavailable",
                       "Subscriptions billed in India need INR billing, which isn't available "
                       "yet. We'll email you when it is.", country=body.country.upper())
    limited = await db.run_sync(lambda s: accounts.hit_rate_limit(
        s, f"checkout:{viewer.user_id}", limit=10, window=timedelta(minutes=10)))
    await db.commit()
    if limited:
        raise ApiError(429, "rate_limited", "Too many checkout attempts. Wait a few minutes.")
    if body.idempotency_key:
        saved = (await db.execute(select(IdempotencyKey.response).where(
            IdempotencyKey.user_id == viewer.user_id, IdempotencyKey.scope == "checkout",
            IdempotencyKey.key == body.idempotency_key))).scalar_one_or_none()
        if saved:
            return CheckoutOut(**saved)

    ent = await db.run_sync(lambda s: accounts.entitlements_for(s, viewer.user_id))
    sub = ent.subscription
    key = f"{viewer.user_id}:{body.plan_id}:{body.interval}:{body.idempotency_key or 'once'}"
    try:
        if sub is not None and ent.source in ("subscription", "grace"):
            if sub.plan_id == body.plan_id and sub.billing_interval == body.interval:
                raise ApiError(409, "already_subscribed", f"You're already on {ent.plan_name}.")
            await anyio.to_thread.run_sync(lambda: provider.change_plan(
                subscription_id=sub.provider_subscription_id, price_id=price,
                idempotency_key=f"change:{key}"))
            out = CheckoutOut(mode="updated")
        else:
            user = await db.get(User, viewer.user_id)
            customer = user.billing_customer_id
            if not customer:
                customer = await anyio.to_thread.run_sync(lambda: provider.create_customer(
                    user_id=str(user.id), email=user.email, phone=user.phone, name=user.name))
                user.billing_customer_id = customer
                await db.commit()
            session = await anyio.to_thread.run_sync(lambda: provider.create_checkout(
                customer_id=customer, price_id=price, user_id=str(viewer.user_id),
                plan_id=body.plan_id, interval=body.interval,
                idempotency_key=f"checkout:{key}"))
            out = CheckoutOut(mode="checkout", url=session.url)
    except BillingError as exc:
        raise ApiError(502, exc.code, exc.message) from exc
    except ApiError:
        raise
    except Exception as exc:
        log.exception("checkout for %s failed", viewer.user_id)
        raise ApiError(502, "billing_failed",
                       "The payment provider couldn't start checkout. Try again.") from exc
    if body.idempotency_key:
        db.add(IdempotencyKey(user_id=viewer.user_id, scope="checkout",
                              key=body.idempotency_key, response=out.model_dump()))
        await db.commit()
    return out


@router.post("/cancel", response_model=CancelOut, summary="Cancel at the period's end")
async def cancel(viewer: Viewer = Depends(get_viewer),
                 db: AsyncSession = Depends(get_db)) -> CancelOut:
    provider = _provider()
    ent = await db.run_sync(lambda s: accounts.entitlements_for(s, viewer.user_id))
    sub = ent.subscription
    if sub is None or ent.source not in ("subscription", "grace"):
        raise ApiError(404, "no_subscription", "There's no active subscription to cancel.")
    await anyio.to_thread.run_sync(lambda: provider.cancel(
        subscription_id=sub.provider_subscription_id))
    # the webhook records it; until then say what was asked for
    return CancelOut(status="cancel_requested", current_period_end=sub.current_period_end)


@router.get("/portal", response_model=BillingLinkOut, summary="Manage billing")
async def portal(viewer: Viewer = Depends(get_viewer),
                 db: AsyncSession = Depends(get_db)) -> BillingLinkOut:
    provider = _provider()
    user = await db.get(User, viewer.user_id)
    if not user or not user.billing_customer_id:
        raise ApiError(404, "no_billing_account", "There's no billing account yet.")
    url = await anyio.to_thread.run_sync(lambda: provider.portal(
        customer_id=user.billing_customer_id))
    return BillingLinkOut(url=url)


@router.post("/webhook", summary="The payment provider's events", include_in_schema=False)
async def webhook(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    provider = _provider()
    payload = await request.body()
    try:
        event = provider.parse_webhook(payload, request.headers.get("stripe-signature"))
    except BillingError as exc:
        log.warning("webhook refused: %s", exc.message)
        return JSONResponse(status_code=400, content={"error": exc.code})

    handled = event.type in HANDLED
    fresh = await db.run_sync(lambda s: accounts.record_event(
        s, event_id=event.id, provider=provider.name, type_=event.type, created=event.created,
        outcome="processed" if handled else "ignored"))
    if not fresh:
        await db.rollback()
        return JSONResponse({"received": True, "duplicate": True})
    if not handled:
        await db.commit()
        return JSONResponse({"received": True, "ignored": True})

    obj = event.object
    at = event.created
    try:
        state, requires_action = None, None
        if event.type.startswith("customer.subscription."):
            state = provider.subscription_state(obj)
        else:
            sub_id = (obj.get("subscription") if event.type == "checkout.session.completed"
                      else provider.invoice_subscription(obj))
            if event.type == "checkout.session.completed" and obj.get("client_reference_id"):
                await _link_customer(db, obj.get("client_reference_id"), obj.get("customer"))
            if sub_id:
                state = await anyio.to_thread.run_sync(lambda: provider.subscription(sub_id))
            # read now, so current; stamped with this event's time, not the clock's,
            # so a subscription event Stripe sent in the same second still applies
            at = event.created
            if event.type == "invoice.payment_action_required":
                requires_action = True
        if state is not None:
            if event.type == "checkout.session.completed" and not state.user_id:
                state.user_id = obj.get("client_reference_id")
            mapped = plan_for_price(state.price_id)
            if mapped is None:
                log.error("webhook %s: price %s is not a configured plan", event.id,
                          state.price_id)
            else:
                plan_id, interval = mapped
                applied = await db.run_sync(lambda s: accounts.apply_subscription(
                    s, provider=provider.name, state=state, plan_id=plan_id,
                    interval=interval, event_id=event.id, event_at=at,
                    requires_action=requires_action))
                if applied is None:
                    log.error("webhook %s: no user for customer %s", event.id, state.customer_id)
        await db.commit()
    except Exception:
        await db.rollback()                  # unrecorded: the provider's retry applies it
        log.exception("webhook %s (%s) failed", event.id, event.type)
        return JSONResponse(status_code=500, content={"error": "processing_failed"})
    log.info("webhook %s %s applied", event.id, event.type)
    return JSONResponse({"received": True})


async def _link_customer(db: AsyncSession, user_id: str, customer_id: str | None) -> None:
    if not customer_id:
        return
    user = await db.run_sync(lambda s: accounts.user_for_billing(s, user_id=user_id,
                                                                 customer_id=None))
    if user is not None and not user.billing_customer_id:
        user.billing_customer_id = customer_id
