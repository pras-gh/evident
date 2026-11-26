"""Who is asking, and what their AI requests cost.

Identity is deliberately minimal: an organization, its users, and API keys.
There was no notion of a customer before this — every endpoint was public — so
this is the smallest model that lets every AI request be attributed and every
organization's usage be kept from every other's.

* `Organization` — a customer, or the internal org background jobs run as.
  Limit columns override the defaults in `config/ai.toml`; null means default.
* `User` — belongs to one organization. Role decides what usage it can see:
  `member` its own, `org_admin` its organization's, `platform_admin` (internal
  org only) everyone's.
* `ApiKey` — only the SHA-256 of the key is stored. The key itself is shown
  once, when created.
* `AIUsageLog` — one row per provider attempt, plus a row for each request
  served from cache or blocked by a limit. Never the prompt or the filing text.
* `AIResponseCache` — finished analyses, so an identical request is not paid
  for twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime,
                        ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at

#: Background jobs (ingest, extraction, rendering) run as this organization, so
#: their spend is attributed and capped like a customer's.
INTERNAL_ORG_SLUG = "parallx-internal"

ROLES = ("member", "org_admin", "platform_admin")
USAGE_STATUSES = ("success", "failed", "fallback", "cached", "blocked")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Overrides of the defaults in config/ai.toml [limits]; null means default.
    daily_requests_per_user: Mapped[Optional[int]] = mapped_column(Integer)
    monthly_tokens: Mapped[Optional[int]] = mapped_column(BigInteger)
    monthly_spend_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    max_input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    max_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[created_at]


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('member','org_admin','platform_admin')", name="role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: null for an account made with a phone number alone
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="member", server_default="member")
    created_at: Mapped[created_at]
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # --- people who sign in (accounts); null for API-key-only users
    #: the identity provider's id for them (Firebase uid)
    auth_uid: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    #: how they first signed in: google | password | phone
    auth_provider: Mapped[Optional[str]] = mapped_column(String(16))
    #: the email as a person, not a spelling: lower case, Gmail's dots and
    #: anyone's +tags gone. One account — and one set of free credits — each.
    email_normalized: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False,
                                                 server_default="false")
    #: E.164, "+919812345678"
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: the billing provider's customer (Stripe), once they have checked out
    billing_customer_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: SHA-256 of the full key, hex; the key itself is never stored
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: the key's first characters, so a person can tell their keys apart
    prefix: Mapped[str] = mapped_column(String(16))
    name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[created_at]
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AIUsageLog(Base):
    """One provider attempt, or one request answered from cache or refused.

    Attempts of one request share `request_id`, so a primary that failed twice
    before a fallback answered reads as three rows: failed, failed, fallback.
    """
    __tablename__ = "ai_usage_logs"
    __table_args__ = (
        CheckConstraint(
            "status in ('success','failed','fallback','cached','blocked')", name="status"),
        Index("ix_ai_usage_logs_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_ai_usage_logs_user_id_created_at", "user_id", "created_at"),
        Index("ix_ai_usage_logs_created_at", "created_at"),
        Index("ix_ai_usage_logs_request_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    #: null for background jobs, which run as the internal organization
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True))
    #: all input tokens, cached ones included
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: the part of `input_tokens` read from the provider's prompt cache
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal(0), server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16))
    #: redacted, and never the prompt
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[created_at]


class AIResponseCache(Base):
    """A finished analysis, keyed by everything that could change its answer:
    filing accession, task, provider and model, prompt version and a hash of
    the input. `scope` is `shared` for analysis of public filing text alone,
    or an organization id when the input could carry anything of theirs."""
    __tablename__ = "ai_response_cache"
    __table_args__ = (
        Index("ix_ai_response_cache_accession", "accession"),
        UniqueConstraint("cache_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: SHA-256 over every component below
    cache_key: Mapped[str] = mapped_column(String(64))
    accession: Mapped[str] = mapped_column(String(32))
    task_type: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(64))
    response_text: Mapped[str] = mapped_column(Text)
    stop_reason: Mapped[str] = mapped_column(String(16))
    #: what producing it cost, so the dashboard can show what the cache saved
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[created_at]
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
