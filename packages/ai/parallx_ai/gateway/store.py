"""Where the gateway reads limits and usage, writes logs, and keeps its cache.

`SqlStore` is the real one. `MemoryStore` holds the same data in lists, for
tests of the router's decisions that do not need a database.

Usage windows are calendar UTC: "today" starts at 00:00 UTC, "this month" on
the 1st. A request counts towards a user's daily limit once it has reached a
provider — answers from cache and refused requests do not.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select, update

from .types import GatewayError

#: statuses that mean a provider was called
CALLED = ("success", "failed", "fallback")


@dataclass(slots=True)
class LogRow:
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    provider: str
    model: str
    task_type: str
    request_id: uuid.UUID
    status: str
    created_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal(0)
    latency_ms: int = 0
    error_message: str | None = None


@dataclass(slots=True)
class Counts:
    user_requests_today: int = 0
    org_tokens_month: int = 0
    org_spend_month: Decimal = Decimal(0)


@dataclass(slots=True)
class CacheEntry:
    cache_key: str
    accession: str
    task_type: str
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    scope: str
    response_text: str
    stop_reason: str
    usage: dict[str, Any]
    estimated_cost_usd: Decimal
    created_at: datetime
    expires_at: datetime | None = None
    hit_count: int = 0


class UnknownOrganization(GatewayError):
    pass


def windows(now: datetime) -> tuple[datetime, datetime, datetime, datetime]:
    """(day start, next day, month start, next month), UTC."""
    now = now.astimezone(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    return day, day + timedelta(days=1), month, next_month


class Store(Protocol):
    def org_overrides(self, organization_id: uuid.UUID) -> dict[str, Any]: ...
    def counts(self, organization_id: uuid.UUID, user_id: uuid.UUID | None,
               now: datetime) -> Counts: ...
    def log(self, row: LogRow) -> None: ...
    def cache_get(self, key: str, now: datetime) -> CacheEntry | None: ...
    def cache_put(self, entry: CacheEntry) -> None: ...


# ------------------------------------------------------------------ memory

@dataclass
class MemoryStore:
    orgs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    rows: list[LogRow] = field(default_factory=list)
    cache: dict[str, CacheEntry] = field(default_factory=dict)

    def org_overrides(self, organization_id):
        if organization_id not in self.orgs:
            raise UnknownOrganization(f"no organization {organization_id}")
        return dict(self.orgs[organization_id])

    def counts(self, organization_id, user_id, now):
        day, _, month, _ = windows(now)
        mine = [r for r in self.rows if r.organization_id == organization_id
                and r.created_at >= month]
        return Counts(
            user_requests_today=len({r.request_id for r in mine
                                     if user_id is not None and r.user_id == user_id
                                     and r.created_at >= day and r.status in CALLED}),
            org_tokens_month=sum(r.input_tokens + r.output_tokens for r in mine),
            org_spend_month=sum((r.estimated_cost_usd for r in mine), Decimal(0)))

    def log(self, row):
        self.rows.append(row)

    def cache_get(self, key, now):
        e = self.cache.get(key)
        if e is None or (e.expires_at is not None and e.expires_at <= now):
            return None
        e.hit_count += 1
        return e

    def cache_put(self, entry):
        self.cache[entry.cache_key] = entry


# --------------------------------------------------------------------- sql

class SqlStore:
    """Backed by Postgres through the project's session factory. Each call is
    its own short transaction, so a log row is committed when the attempt it
    records ends — a crash later in the request does not lose it."""

    def __init__(self, url: str | None = None):
        from parallx_db import session_scope
        self._session = lambda: session_scope(url)

    def org_overrides(self, organization_id):
        from parallx_db import Organization
        with self._session() as db:
            org = db.get(Organization, organization_id)
            if org is None:
                raise UnknownOrganization(f"no organization {organization_id}")
            return {k: getattr(org, k) for k in ("daily_requests_per_user", "monthly_tokens",
                                                 "monthly_spend_usd", "max_input_tokens",
                                                 "max_output_tokens")}

    def counts(self, organization_id, user_id, now):
        from parallx_db import AIUsageLog as L
        day, _, month, _ = windows(now)
        with self._session() as db:
            tokens, spend = db.execute(
                select(func.coalesce(func.sum(L.input_tokens + L.output_tokens), 0),
                       func.coalesce(func.sum(L.estimated_cost_usd), 0))
                .where(L.organization_id == organization_id, L.created_at >= month)).one()
            requests = 0
            if user_id is not None:
                requests = db.execute(
                    select(func.count(func.distinct(L.request_id)))
                    .where(L.user_id == user_id, L.created_at >= day,
                           L.status.in_(CALLED))).scalar_one()
        return Counts(user_requests_today=int(requests), org_tokens_month=int(tokens),
                      org_spend_month=Decimal(str(spend)))

    def log(self, row):
        from parallx_db import AIUsageLog
        with self._session() as db:
            db.add(AIUsageLog(**{k: getattr(row, k) for k in LogRow.__slots__}))

    def cache_get(self, key, now):
        from parallx_db import AIResponseCache as C
        with self._session() as db:
            hit = db.execute(select(C).where(C.cache_key == key)).scalar_one_or_none()
            if hit is None or (hit.expires_at is not None and hit.expires_at <= now):
                return None
            db.execute(update(C).where(C.id == hit.id)
                       .values(hit_count=C.hit_count + 1, last_hit_at=now))
            return CacheEntry(**{k: getattr(hit, k) for k in CacheEntry.__slots__})

    def cache_put(self, entry):
        from sqlalchemy.dialects.postgresql import insert
        from parallx_db import AIResponseCache
        values = {k: getattr(entry, k) for k in CacheEntry.__slots__}
        with self._session() as db:
            db.execute(insert(AIResponseCache).values(**values).on_conflict_do_update(
                index_elements=["cache_key"],
                set_={k: values[k] for k in ("response_text", "stop_reason", "usage",
                                             "estimated_cost_usd", "created_at", "expires_at")}))
