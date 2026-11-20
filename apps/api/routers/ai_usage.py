"""AI usage: what was spent, by whom, on what — for the admin dashboard and
for customers looking at their own.

    GET /v1/me
    GET /v1/ai-usage/summary?date_from&date_to&provider&model&task_type&organization_id&user_id
    GET /v1/ai-usage/limits

Every query goes through `auth.usage_scope`: a member sees their own usage, an
org admin their organization's, a platform admin everyone's. The filters a
caller passes can narrow that, never widen it.

Definitions, all over the selected dates (UTC, inclusive):

- **requests** — distinct request ids, whatever happened to them
- **attempts** — provider calls, retries and fallbacks included
- **failed requests** — requests no provider answered (refused ones are
  counted as blocked instead)
- **fallback rate** — requests a fallback answered, over requests any
  provider answered
- **average cost** — total cost over requests that were answered

With a provider or model filter, counts are of that provider's or model's rows.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import anyio
from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import AIUsageLog as L
from evident_db import Organization

from ..auth import Principal, get_principal, usage_scope
from ..deps import get_db, get_gateway
from ..schemas import (LimitStatus, MeOut, OrganizationOut, UsageBreakdownRow,
                       UsageEventOut, UsageFacets, UsageLimitsOut, UsagePoint,
                       UsageSummaryOut, UsageTotals)

router = APIRouter(tags=["ai usage"])

CALLED = ("success", "failed", "fallback")
ANSWERED = ("success", "fallback", "cached")


@router.get("/me", response_model=MeOut, summary="The caller")
async def me(p: Principal = Depends(get_principal)) -> MeOut:
    return MeOut(user_id=p.user_id, email=p.email, role=p.role,
                 organization=OrganizationOut(id=p.organization_id, slug=p.organization_slug,
                                              name=p.organization_name,
                                              is_internal=p.is_internal))


def _window(date_from: date | None, date_to: date | None) -> tuple[date, date, datetime, datetime]:
    to = date_to or datetime.now(timezone.utc).date()
    frm = date_from or to - timedelta(days=29)
    start = datetime.combine(frm, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return frm, to, start, end


def _breakdown(rows, label=lambda r: str(r.key)) -> list[UsageBreakdownRow]:
    return [UsageBreakdownRow(key=str(r.key), label=label(r), cost_usd=Decimal(str(r.cost)),
                              requests=r.requests, input_tokens=r.input_tokens,
                              output_tokens=r.output_tokens, failed_attempts=r.failed)
            for r in rows]


@router.get("/ai-usage/summary", response_model=UsageSummaryOut, summary="AI spend and reliability")
async def summary(
    p: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
    date_from: date | None = Query(None, description="inclusive, UTC; default 30 days ago"),
    date_to: date | None = Query(None, description="inclusive, UTC; default today"),
    provider: str | None = None,
    model: str | None = None,
    task_type: str | None = None,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> UsageSummaryOut:
    org, user = usage_scope(p, organization_id, user_id)
    frm, to, start, end = _window(date_from, date_to)

    scope = [L.created_at >= start, L.created_at < end]
    if org is not None:
        scope.append(L.organization_id == org)
    if user is not None:
        scope.append(L.user_id == user)
    conds = list(scope)
    if provider:
        conds.append(L.provider == provider)
    if model:
        conds.append(L.model == model)
    if task_type:
        conds.append(L.task_type == task_type)

    distinct_req = func.count(func.distinct(L.request_id))
    t = (await db.execute(select(
        func.coalesce(func.sum(L.estimated_cost_usd), 0).label("cost"),
        distinct_req.label("requests"),
        func.count().filter(L.status.in_(CALLED)).label("attempts"),
        func.coalesce(func.sum(L.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(L.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(L.cached_tokens), 0).label("cached_tokens"),
        func.count().filter(L.status == "failed").label("failed_attempts"),
        distinct_req.filter(L.status == "fallback").label("fallback_requests"),
        func.count().filter(L.status == "cached").label("cache_hits"),
        distinct_req.filter(L.status == "blocked").label("blocked"),
        distinct_req.filter(L.status.in_(ANSWERED)).label("answered"),
        distinct_req.filter(L.status.in_(("success", "fallback"))).label("provider_answered"),
    ).where(*conds))).one()

    unanswered = (select(L.request_id).where(*conds).group_by(L.request_id).having(and_(
        func.bool_or(L.status == "failed"),
        ~func.bool_or(L.status.in_(ANSWERED + ("blocked",))))).subquery())
    failed_requests = (await db.execute(select(func.count()).select_from(unanswered))).scalar_one()

    cost = Decimal(str(t.cost))
    totals = UsageTotals(
        cost_usd=cost, requests=t.requests, attempts=t.attempts, input_tokens=t.input_tokens,
        output_tokens=t.output_tokens, cached_tokens=t.cached_tokens,
        avg_cost_per_request=(cost / t.answered).quantize(Decimal("0.000001")) if t.answered else Decimal(0),
        failed_requests=failed_requests, failed_attempts=t.failed_attempts,
        fallback_requests=t.fallback_requests,
        fallback_rate=round(t.fallback_requests / t.provider_answered, 4) if t.provider_answered else 0.0,
        cache_hits=t.cache_hits, blocked_requests=t.blocked)

    def grouped(*keys):
        return (select(*keys, func.coalesce(func.sum(L.estimated_cost_usd), 0).label("cost"),
                       distinct_req.label("requests"),
                       func.coalesce(func.sum(L.input_tokens), 0).label("input_tokens"),
                       func.coalesce(func.sum(L.output_tokens), 0).label("output_tokens"),
                       func.count().filter(L.status == "failed").label("failed"))
                .where(*conds).group_by(*keys))

    by_provider = (await db.execute(grouped(L.provider.label("key"))
                                    .order_by(func.sum(L.estimated_cost_usd).desc()))).all()
    by_model = (await db.execute(grouped((L.provider + ":" + L.model).label("key"))
                                 .order_by(func.sum(L.estimated_cost_usd).desc()))).all()
    by_task = (await db.execute(grouped(L.task_type.label("key"))
                                .order_by(func.sum(L.estimated_cost_usd).desc()))).all()
    by_org = (await db.execute(
        grouped(L.organization_id.label("key"), Organization.name.label("name"))
        .join(Organization, Organization.id == L.organization_id)
        .order_by(func.sum(L.estimated_cost_usd).desc()))).all()

    async def points(unit: str, fmt: str) -> list[UsagePoint]:
        bucket = func.date_trunc(unit, func.timezone("UTC", L.created_at))
        rows = (await db.execute(
            select(bucket.label("bucket"),
                   func.coalesce(func.sum(L.estimated_cost_usd), 0).label("cost"),
                   distinct_req.label("requests"))
            .where(*conds).group_by(bucket).order_by(bucket))).all()
        return [UsagePoint(period=r.bucket.strftime(fmt), cost_usd=Decimal(str(r.cost)),
                           requests=r.requests) for r in rows]

    recent = (await db.execute(
        select(L, Organization.name)
        .join(Organization, Organization.id == L.organization_id)
        .where(*conds, or_(L.status == "failed", L.status == "fallback", L.status == "blocked"))
        .order_by(L.created_at.desc()).limit(25))).all()

    providers = (await db.execute(select(L.provider).where(*scope).distinct()
                                  .order_by(L.provider))).scalars().all()
    # one labelled expression: DISTINCT needs ORDER BY to name the same one,
    # and two `provider || ':' || model` builds carry different bind params
    model_key = (L.provider + ":" + L.model).label("model_key")
    models = (await db.execute(select(model_key).where(*scope).distinct()
                               .order_by(model_key))).scalars().all()
    orgs = []
    if p.is_platform_admin:
        orgs = [OrganizationOut(id=o.id, slug=o.slug, name=o.name, is_internal=o.is_internal)
                for o in (await db.execute(select(Organization).order_by(Organization.name))).scalars()]

    return UsageSummaryOut(
        organization_id=org, user_id=user, date_from=frm, date_to=to, totals=totals,
        by_provider=_breakdown(by_provider),
        by_model=_breakdown(by_model),
        by_organization=_breakdown(by_org, lambda r: r.name),
        by_task=_breakdown(by_task, lambda r: r.key.replace("_", " ")),
        daily=await points("day", "%Y-%m-%d"),
        monthly=await points("month", "%Y-%m"),
        recent_failures=[UsageEventOut(created_at=r.created_at, request_id=r.request_id,
                                       organization=name, provider=r.provider, model=r.model,
                                       task_type=r.task_type, status=r.status,
                                       error_message=r.error_message) for r, name in recent],
        facets=UsageFacets(providers=list(providers), models=list(models), organizations=orgs))


@router.get("/ai-usage/limits", response_model=UsageLimitsOut,
            summary="Usage limits and how much of each is used")
async def limits(p: Principal = Depends(get_principal),
                 organization_id: uuid.UUID | None = None,
                 gw=Depends(get_gateway)) -> UsageLimitsOut:
    org, _ = usage_scope(p, organization_id, None)
    org = org or p.organization_id
    from evident_ai.gateway import AIContext
    from evident_ai.gateway.store import windows

    def read():
        ctx = AIContext(organization_id=org, user_id=p.user_id, source="api:limits")
        now = datetime.now(timezone.utc)
        return gw.limits(ctx), gw.store.counts(org, p.user_id, now), windows(now)

    lim, counts, (_, next_day, _, next_month) = await anyio.to_thread.run_sync(read)
    return UsageLimitsOut(organization_id=org, limits=[
        LimitStatus(limit="daily_requests_per_user", allowed=lim.daily_requests_per_user,
                    used=counts.user_requests_today, resets_at=next_day),
        LimitStatus(limit="monthly_tokens", allowed=lim.monthly_tokens,
                    used=counts.org_tokens_month, resets_at=next_month),
        LimitStatus(limit="monthly_spend_usd", allowed=lim.monthly_spend_usd,
                    used=counts.org_spend_month, resets_at=next_month),
        LimitStatus(limit="max_input_tokens", allowed=lim.max_input_tokens, used=0),
        LimitStatus(limit="max_output_tokens", allowed=lim.max_output_tokens, used=0),
    ])
