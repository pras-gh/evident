"""Signing in, who you are, what your plan allows, and starting research.

    POST /v1/auth/session      a verified identity-provider token → a session cookie
    POST /v1/auth/logout       revoke it
    GET  /v1/me                the caller (a session, or an API key)
    GET  /v1/me/usage          companies and AI queries used, and left
    GET  /v1/me/subscription   the plan, and its billing state
    GET  /v1/plans             Free, Iix, IIx — public
    POST /v1/research/access   research a company: spends a credit the first time

A company credit is spent the first time a person researches a company, and
never again for it: not on a second search, a refresh, or a return visit. A
company whose filings must first be fetched is held `pending`; if the fetch
fails, the credit comes back (`fetch_worker` → `accounts.settle_pending`).
Every rule is enforced here and in `parallx_db.accounts`, never in the page.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import anyio
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import AuthSession, Company, Document, Organization, Plan, User
from parallx_db import accounts

from ..deps import get_db
from ..errors import ApiError
from ..identity import Identity, IdentityError, get_verifier, normalize_email
from ..schemas import (CompanyUsageOut, MeOut, OrganizationOut, PlanFeatureOut, PlanOut,
                       ResearchAccessIn, ResearchAccessOut, SessionIn, SessionOut,
                       SubscriptionOut, UsageOut)
from ..viewer import (COOKIE, Viewer, clear_session_cookie, create_session, get_viewer,
                      hash_token, optional_viewer, set_session_cookie)

router = APIRouter(tags=["account"])
log = logging.getLogger("parallx.account")

# ------------------------------------------------------------------ plans copy

#: What each plan says, in order. `available` is whether it works in the
#: product today: a feature that does not is shown as coming, never as included.
PLAN_COPY = {
    "free": ("Research two companies, free. No payment required.", [
        ("2 company research credits", True),
        ("SEC filing intelligence", True),
        ("Historical company timelines", True),
        ("Source-linked evidence viewer", True),
        ("{ai} AI research queries to start", True),
    ]),
    "investor": ("For curious investors who want to research smarter.", [
        ("Track up to {companies} companies", True),
        ("Management Promise Tracker", False),
        ("Historical company timelines", True),
        ("SEC filing intelligence", True),
        ("AI research assistant", True),
        ("Source-linked evidence viewer", True),
        ("Weekly change alerts", False),
        ("{ai} AI research queries/month", True),
    ]),
    "analyst": ("For serious investors and independent equity analysts.", [
        ("Track up to {companies} companies", True),
        ("Everything in Iix", True),
        ("{ai} AI research queries/month", True),
        ("Advanced commitment comparisons", False),
        ("Multi-year management narrative analysis", False),
        ("Custom commitment alerts", False),
        ("Daily change monitoring", False),
        ("Exportable research reports", False),
        ("Earnings transcript intelligence where licensed", False),
    ]),
}


def plan_out(p: Plan) -> PlanOut:
    from ..billing import price_id
    desc, feats = PLAN_COPY[p.id]
    ai = accounts.free_ai_allowance() if p.id == "free" else p.monthly_ai_queries
    return PlanOut(
        id=p.id, name=p.name, audience=p.audience, description=desc,
        monthly_price=p.monthly_price, annual_price=p.annual_price,
        max_companies=p.max_companies, monthly_ai_queries=ai,
        recommended=p.id == "analyst",
        features=[PlanFeatureOut(label=f.format(companies=p.max_companies, ai=ai),
                                 available=ok) for f, ok in feats],
        entitlements=dict(p.entitlements),
        purchasable=p.id != "free" and bool(price_id(p.id, "month") and price_id(p.id, "year")))


@router.get("/plans", response_model=list[PlanOut], summary="The plans")
async def plans(db: AsyncSession = Depends(get_db)) -> list[PlanOut]:
    rows = (await db.execute(select(Plan).order_by(Plan.sort))).scalars().all()
    return [plan_out(p) for p in rows if p.id in PLAN_COPY]


# -------------------------------------------------------------------- sessions


def _me(v: Viewer) -> MeOut:
    return MeOut(user_id=v.user_id, email=v.email, role=v.role, phone=v.phone, name=v.name,
                 auth_provider=v.auth_provider, via=v.via,
                 organization=OrganizationOut(id=v.organization_id, slug=v.organization_slug,
                                              name=v.organization_name,
                                              is_internal=v.is_internal))


async def _find_or_create(db: AsyncSession, ident: Identity) -> tuple[User, bool]:
    """One account per person: matched by the provider's uid, then by the
    normalised email (verified emails only), then by phone. Only then made."""
    email_key = normalize_email(ident.email) if ident.email else None
    user = (await db.execute(select(User).where(User.auth_uid == ident.uid))).scalar_one_or_none()
    if user is None and email_key and ident.email_verified:
        user = (await db.execute(select(User).where(
            User.email_normalized == email_key))).scalar_one_or_none()
    if user is None and ident.phone:
        user = (await db.execute(select(User).where(User.phone == ident.phone))
                ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if user is not None:
        user.last_login_at, user.updated_at = now, now
        if user.auth_uid is None:
            user.auth_uid = ident.uid
        if ident.phone and not user.phone:
            user.phone = ident.phone
        if ident.name and not user.name:
            user.name = ident.name
        if email_key and ident.email_verified and not user.email_verified \
                and user.email_normalized == email_key:
            user.email_verified = True
        return user, False
    if email_key and (await db.execute(select(User.id).where(
            User.email_normalized == email_key))).first():
        # the address belongs to an account, and this sign-in has not proven it
        raise ApiError(409, "account_exists",
                       "An account already uses this email. Sign in with it instead.")
    org = Organization(slug=f"u-{uuid.uuid4().hex[:12]}",
                       name=ident.name or ident.email or ident.phone or "Personal")
    db.add(org)
    await db.flush()
    user = User(organization_id=org.id, email=ident.email.strip().lower() if ident.email else None,
                email_normalized=email_key, email_verified=ident.email_verified,
                phone=ident.phone, name=ident.name, role="member", auth_uid=ident.uid,
                auth_provider=ident.provider, last_login_at=now, updated_at=now)
    db.add(user)
    await db.flush()
    log.info("account created: %s via %s", user.id, ident.provider)
    return user, True


@router.post("/auth/session", response_model=SessionOut,
             summary="Sign in: a verified identity token for a session")
async def sign_in(body: SessionIn, request: Request, response: Response,
                  db: AsyncSession = Depends(get_db)) -> SessionOut:
    try:
        ident = await anyio.to_thread.run_sync(lambda: get_verifier().verify(body.id_token))
    except IdentityError as exc:
        status = 503 if exc.code == "not_configured" else 401
        raise ApiError(status, exc.code, exc.message) from exc
    limited = await db.run_sync(lambda s: accounts.hit_rate_limit(
        s, f"auth:{ident.uid}", limit=20, window=timedelta(minutes=10)))
    await db.commit()
    if limited:
        raise ApiError(429, "rate_limited", "Too many sign-ins. Wait a few minutes.")
    if ident.provider == "password" and not ident.email_verified:
        raise ApiError(403, "email_unverified",
                       "Verify your email first: we sent a link to your inbox.")
    if ident.provider == "phone" and not ident.phone:
        raise ApiError(401, "invalid_token", "No phone number on this sign-in.")
    user, is_new = await _find_or_create(db, ident)
    token, _ = await create_session(db, user.id, user_agent=request.headers.get("user-agent"))
    org = await db.get(Organization, user.organization_id)
    await db.commit()
    set_session_cookie(response, token)
    v = Viewer(user.id, user.email, user.phone, user.name, user.role, user.auth_provider,
               org.id, org.slug, org.name, org.is_internal, "session")
    return SessionOut(user=_me(v), is_new=is_new)


@router.post("/auth/logout", status_code=204, summary="Sign out")
async def sign_out(request: Request, response: Response,
                   db: AsyncSession = Depends(get_db)) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        row = (await db.execute(select(AuthSession).where(
            AuthSession.token_hash == hash_token(token)))).scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            await db.commit()
    out = Response(status_code=204)
    clear_session_cookie(out)
    return out


@router.get("/me", response_model=MeOut, summary="The caller")
async def me(viewer: Viewer = Depends(get_viewer)) -> MeOut:
    return _me(viewer)


# --------------------------------------------------------------------- usage


async def usage_out(db: AsyncSession, viewer: Viewer, *, companies: bool = True) -> UsageOut:
    def read(s):
        ent = accounts.entitlements_for(s, viewer.user_id, staff=viewer.is_staff)
        u = accounts.usage_for(s, viewer.user_id, ent)
        rows = accounts.researched_companies(s, viewer.user_id) if companies else []
        return ent, u, rows
    ent, u, rows = await db.run_sync(read)
    limit = (u.free_credits_total if ent.source == "free" else ent.max_companies)
    return UsageOut(
        plan_id=ent.plan_id, plan_name=ent.plan_name, source=ent.source,
        companies_used=u.free_credits_used if ent.source == "free" else u.companies,
        companies_limit=limit, companies_left=u.companies_left,
        free_credits_used=u.free_credits_used, free_credits_total=u.free_credits_total,
        ai_queries_used=u.ai_queries_used, ai_queries_limit=ent.ai_queries,
        ai_queries_left=u.ai_queries_left, period_end=ent.period_end,
        companies=[CompanyUsageOut(ticker=c.ticker, name=c.name,
                                   first_researched_at=a.first_researched_at,
                                   last_accessed_at=a.last_accessed_at, status=a.status,
                                   access_source=a.access_source) for c, a in rows])


@router.get("/me/usage", response_model=UsageOut, summary="What you have used, and have left")
async def my_usage(viewer: Viewer = Depends(get_viewer),
                   db: AsyncSession = Depends(get_db)) -> UsageOut:
    return await usage_out(db, viewer)


@router.get("/me/subscription", response_model=SubscriptionOut, summary="Your plan")
async def my_subscription(viewer: Viewer = Depends(get_viewer),
                          db: AsyncSession = Depends(get_db)) -> SubscriptionOut:
    def read(s):
        return accounts.entitlements_for(s, viewer.user_id, staff=viewer.is_staff), \
            s.get(User, viewer.user_id)
    ent, user = await db.run_sync(read)
    sub = ent.subscription
    return SubscriptionOut(
        plan_id=ent.plan_id, plan_name=ent.plan_name, source=ent.source,
        status=sub.status if sub else None,
        billing_interval=sub.billing_interval if sub else None,
        current_period_end=sub.current_period_end if sub else None,
        cancel_at_period_end=bool(sub and sub.cancel_at_period_end),
        grace_until=ent.grace_until, requires_action=bool(sub and sub.requires_action),
        features=ent.features, has_billing_account=bool(user and user.billing_customer_id))


# ------------------------------------------------------------------ research


async def resolve_company(db: AsyncSession, *, ticker: str | None,
                          cik: str | None) -> tuple[Company, bool]:
    """(company, has filings here). A company not on record is looked up on
    EDGAR and recorded, so it can be fetched."""
    stmt = select(Company)
    if cik:
        stmt = stmt.where(Company.cik == cik.zfill(10))
    else:
        stmt = stmt.where(Company.ticker == (ticker or "").strip().upper().lstrip("$"))
    company = (await db.execute(stmt)).scalar_one_or_none()
    if company is None:
        from parallx_parser import edgar
        if not edgar.has_user_agent():
            raise ApiError(404, "unknown_company", "Parallx has no filings for that company yet.")
        try:
            sec = await anyio.to_thread.run_sync(
                lambda: edgar.lookup(ticker=ticker) if not cik else next(
                    (c for c in edgar.companies() if c.cik == cik.zfill(10)), None))
        except Exception as exc:
            raise ApiError(503, "edgar_unavailable", "EDGAR could not be reached.") from exc
        if sec is None:
            raise ApiError(404, "unknown_company", "The SEC has no filer by that name or ticker.")
        from parallx_db.repositories import upsert_company
        company = await db.run_sync(lambda s: upsert_company(s, cik=sec.cik, name=sec.name,
                                                             ticker=sec.ticker))
        await db.flush()
    has_docs = (await db.execute(select(Document.id).where(
        Document.company_id == company.id).limit(1))).first() is not None
    return company, has_docs


async def start_research(db: AsyncSession, viewer: Viewer, company: Company, *,
                         has_docs: bool, idempotency_key: str | None = None):
    """Access to `company` for `viewer`, spending what it costs. Commits.
    Raises the paywall (402) when the plan has nothing left."""
    key = f"research:{viewer.user_id}:{idempotency_key}" if idempotency_key else None
    result = await db.run_sync(lambda s: accounts.grant_access(
        s, viewer.user_id, company.id, staff=viewer.is_staff, pending=not has_docs,
        idempotency_key=key))
    await db.commit()
    if result.outcome == "denied":
        usage = await usage_out(db, viewer)
        message = ("You've used your 2 free company research credits." if
                   result.reason == "free_credits_used" else
                   f"Your plan covers {usage.companies_limit} companies, and you're "
                   f"researching {usage.companies_used}.")
        raise ApiError(402, "upgrade_required", message, reason=result.reason,
                       ticker=company.ticker, name=company.name,
                       usage=usage.model_dump(mode="json"))
    return result


@router.post("/research/access", response_model=ResearchAccessOut,
             summary="Start researching a company")
async def research_access(body: ResearchAccessIn, viewer: Viewer = Depends(get_viewer),
                          db: AsyncSession = Depends(get_db)) -> ResearchAccessOut:
    if not body.ticker and not body.cik:
        raise ApiError(422, "company_required", "Say which company: a ticker or a CIK.")
    limited = await db.run_sync(lambda s: accounts.hit_rate_limit(
        s, f"research:{viewer.user_id}", limit=60, window=timedelta(minutes=1)))
    await db.commit()
    if limited:
        raise ApiError(429, "rate_limited", "Too many requests. Wait a minute.")
    company, has_docs = await resolve_company(db, ticker=body.ticker, cik=body.cik)
    result = await start_research(db, viewer, company, has_docs=has_docs,
                                  idempotency_key=body.idempotency_key)
    fetch_id = None
    if result.status == "pending":
        from .fetches import request_fetch
        f = await request_fetch(db, cik=company.cik, ticker=company.ticker or "",
                                name=company.name)
        fetch_id = f.id
    return ResearchAccessOut(outcome=result.outcome, status=result.status,
                             ticker=company.ticker, name=company.name, fetch_id=fetch_id,
                             usage=await usage_out(db, viewer))
