"""Accounts, plans, subscriptions, and what each user may research.

Tables (migration 0018):

* `sessions` — a signed-in browser: a random token, stored as its SHA-256,
  with an absolute and an idle expiry.
* `plans` — Free, Iix (investor), IIx (analyst): prices, limits, features.
* `subscriptions` — a user's paid plan as the billing provider reports it.
  Only verified webhooks write here.
* `user_company_access` — the companies a user has researched. A row is what
  a credit bought; it is never taken away by a plan lapsing.
* `usage_ledger` — every deduction and refund, append-only: free company
  credits, paid company slots, AI research queries, billing-period resets.
* `billing_events` — the provider's event ids already applied, so a webhook
  delivered twice is applied once.
* `idempotency_keys`, `rate_limits` — request replay protection and per-key
  rate limits.

The entitlement rules live here, as sync functions over a `Session`, so the
API (through `AsyncSession.run_sync`) and the workers (a failed fetch refunds
its credit) apply the same ones. Every deduction takes the user's row lock
(`SELECT … FOR UPDATE` on `users`), so concurrent requests are serialised per
user and cannot spend the same credit twice.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func, select, text)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base, created_at

PLAN_IDS = ("free", "investor", "analyst")
#: Statuses the billing provider reports that grant a paid plan outright.
PAID_STATUSES = ("active", "trialing")
#: ...and that grant it until the grace period after the period ends.
GRACE_STATUSES = ("past_due",)
SUB_STATUSES = ("incomplete", "incomplete_expired", "trialing", "active", "past_due",
                "canceled", "unpaid", "paused")
ACCESS_STATUSES = ("pending", "active", "refunded")
LEDGER_KINDS = ("free_company_credit", "paid_company", "ai_query", "period_reset", "refund")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ tables


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    #: SHA-256 of the cookie's token, hex; the token itself is never stored
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[created_at]
    #: signed out after this however active
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: ...or after this without a request; each request pushes it on
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    audience: Mapped[str] = mapped_column(String(128))
    #: USD cents; the price charged is the billing provider's, these are for display
    monthly_price: Mapped[int] = mapped_column(Integer)
    annual_price: Mapped[int] = mapped_column(Integer)
    #: free: lifetime unique-company credits; paid: distinct companies while subscribed
    max_companies: Mapped[int] = mapped_column(Integer)
    #: free: a lifetime onboarding allowance; paid: per billing period
    monthly_ai_queries: Mapped[int] = mapped_column(Integer)
    entitlements: Mapped[dict] = mapped_column(JSONB)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("status in ('incomplete','incomplete_expired','trialing','active',"
                        "'past_due','canceled','unpaid','paused')", name="status"),
        CheckConstraint("billing_interval in ('month','year')", name="billing_interval"),
        Index("ix_subscriptions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    billing_provider: Mapped[str] = mapped_column(String(16))
    provider_customer_id: Mapped[str] = mapped_column(String(255))
    provider_subscription_id: Mapped[str] = mapped_column(String(255), unique=True)
    provider_price_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24))
    billing_interval: Mapped[str] = mapped_column(String(8))
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False,
                                                       server_default="false")
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: a payment needs the customer (3-D Secure, a mandate) — say so in the app
    requires_action: Mapped[bool] = mapped_column(Boolean, default=False,
                                                  server_default="false")
    last_event_id: Mapped[Optional[str]] = mapped_column(String(255))
    #: when the provider created the last event applied: an older one is stale
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=text("now()"), onupdate=_now)


class CompanyAccess(Base):
    __tablename__ = "user_company_access"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id",
                         name="uq_user_company_access_user_id_company_id"),
        CheckConstraint("status in ('pending','active','refunded')", name="status"),
        CheckConstraint("access_source in ('free_credit','plan','staff')", name="access_source"),
        Index("ix_user_company_access_user_id_last_accessed_at", "user_id", "last_accessed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    first_researched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"))
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"))
    access_source: Mapped[str] = mapped_column(String(16))
    #: pending while its filings are fetched; a failed fetch refunds it
    status: Mapped[str] = mapped_column(String(16))


class UsageLedger(Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        CheckConstraint("kind in ('free_company_credit','paid_company','ai_query',"
                        "'period_reset','refund')", name="kind"),
        Index("ix_usage_ledger_user_id_kind_created_at", "user_id", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    #: +1 a use, -1 a refund of one (on the refunded kind, in `detail`)
    quantity: Mapped[int] = mapped_column(Integer)
    company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"))
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"))
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: one deduction per key, ever: a retried request is not charged twice
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(160), unique=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[created_at]


class BillingEvent(Base):
    __tablename__ = "billing_events"

    #: the provider's event id
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(64))
    event_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[created_at]
    outcome: Mapped[str] = mapped_column(String(16))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "scope", "key",
                                       name="uq_idempotency_keys_user_id_scope_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(128))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[created_at]


class RateLimit(Base):
    __tablename__ = "rate_limits"

    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------- entitlements


#: How long a failed payment keeps the paid plan, past the period's end, while
#: the provider retries the card.
def grace_period() -> timedelta:
    return timedelta(days=int(os.environ.get("BILLING_GRACE_DAYS", "7")))


def free_ai_allowance() -> int:
    """AI research queries a free account gets, once: enough to try it."""
    return int(os.environ.get("FREE_AI_QUERIES", "10"))


@dataclass
class Entitlements:
    plan_id: str
    plan_name: str
    #: free | subscription | grace | staff
    source: str
    #: free: lifetime credits; paid: distinct companies; None: unlimited (staff)
    max_companies: Optional[int]
    ai_queries: Optional[int]
    #: the window AI queries are counted in; None for the free allowance
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    features: dict = field(default_factory=dict)
    subscription: Optional[Subscription] = None
    #: paid features last until then, while a failed payment is retried
    grace_until: Optional[datetime] = None


@dataclass
class Usage:
    entitlements: Entitlements
    #: distinct companies the user can open (pending or active)
    companies: int
    free_credits_used: int
    free_credits_total: int
    ai_queries_used: int

    @property
    def companies_left(self) -> Optional[int]:
        e = self.entitlements
        if e.max_companies is None:
            return None
        if e.source == "free":
            return max(0, self.free_credits_total - self.free_credits_used)
        return max(0, e.max_companies - self.companies)

    @property
    def ai_queries_left(self) -> Optional[int]:
        limit = self.entitlements.ai_queries
        return None if limit is None else max(0, limit - self.ai_queries_used)


def current_subscription(db: Session, user_id: uuid.UUID) -> Optional[Subscription]:
    """The user's most relevant subscription: a live one before an ended one,
    the latest period first."""
    rows = db.execute(select(Subscription).where(Subscription.user_id == user_id)
                      .order_by(Subscription.current_period_end.desc().nulls_last(),
                                Subscription.created_at.desc())).scalars().all()
    live = [s for s in rows if s.status in PAID_STATUSES + GRACE_STATUSES]
    return (live or rows or [None])[0]


def entitlements_for(db: Session, user_id: uuid.UUID, *, staff: bool = False,
                     now: Optional[datetime] = None) -> Entitlements:
    now = now or _now()
    plans = {p.id: p for p in db.execute(select(Plan)).scalars()}
    free = plans["free"]
    if staff:
        return Entitlements("staff", "Staff", "staff", None, None,
                            features={k: True for k in free.entitlements})
    sub = current_subscription(db, user_id)
    if sub is not None and sub.plan_id in plans:
        plan = plans[sub.plan_id]
        grace_until = (sub.current_period_end + grace_period()
                       if sub.current_period_end else None)
        paid = sub.status in PAID_STATUSES or (
            sub.status in GRACE_STATUSES and grace_until is not None and now < grace_until)
        if paid:
            return Entitlements(
                plan.id, plan.name, "subscription" if sub.status in PAID_STATUSES else "grace",
                plan.max_companies, plan.monthly_ai_queries,
                period_start=sub.current_period_start, period_end=sub.current_period_end,
                features=dict(plan.entitlements), subscription=sub,
                grace_until=grace_until if sub.status in GRACE_STATUSES else None)
    return Entitlements("free", free.name, "free", free.max_companies, free_ai_allowance(),
                        features=dict(free.entitlements), subscription=sub)


def _sum(db: Session, user_id, kind: str, *, since=None, until=None) -> int:
    stmt = select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
        UsageLedger.user_id == user_id, UsageLedger.kind == kind)
    if since is not None:
        stmt = stmt.where(UsageLedger.created_at >= since)
    if until is not None:
        stmt = stmt.where(UsageLedger.created_at < until)
    return int(db.execute(stmt).scalar_one())


def usage_for(db: Session, user_id: uuid.UUID, ent: Entitlements) -> Usage:
    companies = db.execute(select(func.count()).select_from(CompanyAccess).where(
        CompanyAccess.user_id == user_id,
        CompanyAccess.status.in_(("pending", "active")))).scalar_one()
    free_total = db.execute(select(Plan.max_companies).where(Plan.id == "free")).scalar_one()
    if ent.source == "free":
        ai = _sum(db, user_id, "ai_query")
    else:
        ai = _sum(db, user_id, "ai_query", since=ent.period_start, until=ent.period_end)
    return Usage(ent, companies, _sum(db, user_id, "free_company_credit"), free_total, ai)


@dataclass
class AccessResult:
    #: existing | granted | denied
    outcome: str
    status: Optional[str] = None
    source: Optional[str] = None
    reason: Optional[str] = None
    usage: Optional[Usage] = None


def _lock_user(db: Session, user_id) -> None:
    """Serialise this user's deductions: concurrent requests queue here."""
    db.execute(text("select id from users where id = :u for update"), {"u": user_id})


def has_access(db: Session, user_id, company_id: int) -> bool:
    return db.execute(select(CompanyAccess.id).where(
        CompanyAccess.user_id == user_id, CompanyAccess.company_id == company_id,
        CompanyAccess.status.in_(("pending", "active")))).first() is not None


def grant_access(db: Session, user_id, company_id: int, *, staff: bool = False,
                 pending: bool = False, idempotency_key: Optional[str] = None,
                 now: Optional[datetime] = None) -> AccessResult:
    """Research access to one company: already held (free, any number of
    times), granted (one free credit, or one of the plan's company slots), or
    denied with the usage that says why. `pending` grants it while the
    company's filings are fetched; `settle_pending` keeps or refunds it."""
    _lock_user(db, user_id)
    row = db.execute(select(CompanyAccess).where(
        CompanyAccess.user_id == user_id, CompanyAccess.company_id == company_id)
    ).scalar_one_or_none()
    stamp = now or _now()
    ent = entitlements_for(db, user_id, staff=staff, now=now)
    if row is not None and row.status in ("pending", "active"):
        row.last_accessed_at = stamp
        if row.status == "pending" and not pending:
            row.status = "active"
        return AccessResult("existing", row.status, row.access_source,
                            usage=usage_for(db, user_id, ent))
    usage = usage_for(db, user_id, ent)
    if ent.source == "staff":
        source, kind = "staff", None
    elif ent.source == "free":
        if usage.free_credits_used >= usage.free_credits_total:
            return AccessResult("denied", reason="free_credits_used", usage=usage)
        source, kind = "free_credit", "free_company_credit"
    else:
        if usage.companies >= (ent.max_companies or 0):
            return AccessResult("denied", reason="company_limit", usage=usage)
        source, kind = "plan", "paid_company"
    status = "pending" if pending else "active"
    if row is None:
        row = CompanyAccess(user_id=user_id, company_id=company_id, access_source=source,
                            status=status, first_researched_at=stamp, last_accessed_at=stamp)
        db.add(row)
    else:                                          # refunded before: research it again
        row.status, row.access_source, row.last_accessed_at = status, source, stamp
    if kind:
        db.add(UsageLedger(user_id=user_id, kind=kind, quantity=1, company_id=company_id,
                           subscription_id=ent.subscription.id if ent.subscription and
                           kind == "paid_company" else None,
                           idempotency_key=idempotency_key,
                           detail={"status": status}))
    db.flush()
    return AccessResult("granted", status, source, usage=usage_for(db, user_id, ent))


def settle_pending(db: Session, company_id: int, *, ok: bool) -> int:
    """A company's filings were fetched (`ok`) or could not be: its pending
    access rows become active, or are refunded — the credit or slot given
    back in the ledger. Returns the rows settled."""
    rows = db.execute(select(CompanyAccess).where(
        CompanyAccess.company_id == company_id, CompanyAccess.status == "pending")
        .with_for_update()).scalars().all()
    for r in rows:
        if ok:
            r.status = "active"
            continue
        r.status = "refunded"
        if r.access_source in ("free_credit", "plan"):
            kind = "free_company_credit" if r.access_source == "free_credit" else "paid_company"
            db.add(UsageLedger(user_id=r.user_id, kind=kind, quantity=-1, company_id=company_id,
                               detail={"refund": "research job failed"}))
            db.add(UsageLedger(user_id=r.user_id, kind="refund", quantity=1,
                               company_id=company_id, detail={"refunded_kind": kind}))
    db.flush()
    return len(rows)


def consume_ai_query(db: Session, user_id, *, staff: bool = False,
                     idempotency_key: Optional[str] = None,
                     now: Optional[datetime] = None) -> tuple[bool, Usage]:
    """One AI research query, if the plan has one left this period."""
    _lock_user(db, user_id)
    ent = entitlements_for(db, user_id, staff=staff, now=now)
    usage = usage_for(db, user_id, ent)
    if idempotency_key and db.execute(select(UsageLedger.id).where(
            UsageLedger.idempotency_key == idempotency_key)).first():
        return True, usage
    if ent.ai_queries is not None and usage.ai_queries_used >= ent.ai_queries:
        return False, usage
    db.add(UsageLedger(user_id=user_id, kind="ai_query", quantity=1,
                       subscription_id=ent.subscription.id if ent.source != "free"
                       and ent.subscription else None,
                       period_start=ent.period_start, period_end=ent.period_end,
                       idempotency_key=idempotency_key))
    db.flush()
    return True, usage_for(db, user_id, ent)


def refund_ai_query(db: Session, user_id, *, reason: str) -> None:
    """An AI query that produced no answer is not charged."""
    db.add(UsageLedger(user_id=user_id, kind="ai_query", quantity=-1, detail={"refund": reason}))
    db.add(UsageLedger(user_id=user_id, kind="refund", quantity=1,
                       detail={"refunded_kind": "ai_query", "reason": reason}))
    db.flush()


def researched_companies(db: Session, user_id, *, limit: int = 50) -> list[tuple]:
    """(company, access) the user has researched, most recent first."""
    from .models import Company
    return db.execute(
        select(Company, CompanyAccess).join(CompanyAccess, CompanyAccess.company_id == Company.id)
        .where(CompanyAccess.user_id == user_id,
               CompanyAccess.status.in_(("pending", "active")))
        .order_by(CompanyAccess.last_accessed_at.desc()).limit(limit)).all()


def hit_rate_limit(db: Session, bucket: str, *, limit: int, window: timedelta,
                   now: Optional[datetime] = None) -> bool:
    """Counts one request in `bucket`'s current window; True when it is over
    `limit`. Fixed windows, in the database, so every API process shares them."""
    now = now or _now()
    secs = int(window.total_seconds())
    start = datetime.fromtimestamp(int(now.timestamp()) // secs * secs, tz=timezone.utc)
    count = db.execute(
        insert(RateLimit).values(bucket=bucket, window_start=start, count=1)
        .on_conflict_do_update(index_elements=["bucket", "window_start"],
                               set_={"count": RateLimit.count + 1})
        .returning(RateLimit.count)).scalar_one()
    return count > limit


# ------------------------------------------------------------------ billing


def record_event(db: Session, *, event_id: str, provider: str, type_: str,
                 created: Optional[datetime], outcome: str = "processed") -> bool:
    """Marks a provider event applied; False when it already was — a webhook
    delivered twice is applied once. Commit with the event's effects, so a
    failure part-way leaves it unrecorded and the provider's retry applies it."""
    inserted = db.execute(
        insert(BillingEvent).values(id=event_id, provider=provider, type=type_,
                                    event_created_at=created, outcome=outcome)
        .on_conflict_do_nothing(index_elements=["id"]).returning(BillingEvent.id)
    ).scalar_one_or_none()
    return inserted is not None


def user_for_billing(db: Session, *, user_id: Optional[str], customer_id: Optional[str]):
    from .ai import User
    if user_id:
        try:
            u = db.get(User, uuid.UUID(str(user_id)))
        except ValueError:
            u = None
        if u is not None:
            return u
    if customer_id:
        return db.execute(select(User).where(User.billing_customer_id == customer_id)
                          ).scalar_one_or_none()
    return None


def apply_subscription(db: Session, *, provider: str, state, plan_id: str, interval: str,
                       event_id: Optional[str], event_at: Optional[datetime],
                       requires_action: Optional[bool] = None) -> Optional[Subscription]:
    """Brings `subscriptions` in line with what the provider says now. An
    event older than the last one applied is stale and changes nothing.
    Returns the row, or None when no user can be matched to it."""
    user = user_for_billing(db, user_id=state.user_id, customer_id=state.customer_id)
    if user is None:
        return None
    if state.customer_id and user.billing_customer_id != state.customer_id:
        user.billing_customer_id = state.customer_id
    sub = db.execute(select(Subscription).where(
        Subscription.provider_subscription_id == state.subscription_id).with_for_update()
    ).scalar_one_or_none()
    if sub is not None and sub.last_event_at and event_at and event_at < sub.last_event_at:
        return sub
    new_period = sub is None or sub.current_period_start != state.period_start
    if sub is None:
        sub = Subscription(user_id=user.id, billing_provider=provider,
                           provider_customer_id=state.customer_id,
                           provider_subscription_id=state.subscription_id)
        db.add(sub)
    sub.plan_id, sub.billing_interval = plan_id, interval
    sub.provider_price_id = state.price_id
    sub.status = state.status if state.status in SUB_STATUSES else "incomplete"
    sub.current_period_start, sub.current_period_end = state.period_start, state.period_end
    sub.cancel_at_period_end, sub.canceled_at = state.cancel_at_period_end, state.canceled_at
    if requires_action is not None:
        sub.requires_action = requires_action
    elif sub.status in PAID_STATUSES:
        sub.requires_action = False
    sub.last_event_id, sub.last_event_at = event_id, event_at or _now()
    db.flush()
    if new_period and sub.status in PAID_STATUSES and state.period_start:
        # AI queries count per period; the reset is implicit, the record explicit
        db.add(UsageLedger(user_id=user.id, kind="period_reset", quantity=0,
                           subscription_id=sub.id, period_start=state.period_start,
                           period_end=state.period_end,
                           detail={"plan": plan_id, "event": event_id}))
        db.flush()
    return sub


def mark_requires_action(db: Session, provider_subscription_id: str, flag: bool = True) -> None:
    db.execute(Subscription.__table__.update().where(
        Subscription.provider_subscription_id == provider_subscription_id
    ).values(requires_action=flag))


DEFAULT_PLANS = {
    "free": dict(name="Free", audience="Try Parallx on two companies", monthly_price=0,
                 annual_price=0, max_companies=2, monthly_ai_queries=10, sort=0,
                 entitlements={"promise_tracker": "basic", "timelines": "basic",
                               "evidence_viewer": True, "weekly_alerts": False,
                               "daily_monitoring": False, "advanced_comparisons": False,
                               "exports": False, "transcripts": False}),
    "investor": dict(name="Iix", audience="For investors", monthly_price=1900,
                     annual_price=19000, max_companies=10, monthly_ai_queries=100, sort=1,
                     entitlements={"promise_tracker": "full", "timelines": "full",
                                   "evidence_viewer": True, "weekly_alerts": True,
                                   "daily_monitoring": False, "advanced_comparisons": False,
                                   "exports": False, "transcripts": False}),
    "analyst": dict(name="IIx", audience="For analysts", monthly_price=3900,
                    annual_price=39000, max_companies=50, monthly_ai_queries=500, sort=2,
                    entitlements={"promise_tracker": "full", "timelines": "full",
                                  "evidence_viewer": True, "weekly_alerts": True,
                                  "daily_monitoring": True, "advanced_comparisons": True,
                                  "exports": True, "transcripts": "where_licensed"}),
}


def ensure_plans(db: Session) -> None:
    """The three plans, as migration 0018 seeds them — for databases made by
    `create_all` (tests) rather than migrations."""
    for pid, values in DEFAULT_PLANS.items():
        db.execute(insert(Plan).values(id=pid, **values).on_conflict_do_nothing(
            index_elements=["id"]))
