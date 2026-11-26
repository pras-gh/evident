"""accounts, plans, subscriptions, research access and the usage ledger

People sign in (Firebase Authentication) and research companies: two free
company credits for life, then a plan — Iix ($19/month, 10 companies) or IIx
($39/month, 50) — billed through Stripe. See packages/db/parallx_db/accounts.py.

* `users` gains what a person who signs in needs: the identity provider's uid,
  how they signed in, a normalised email (one account, and one set of free
  credits, per person), a phone number, and their billing customer. Email
  becomes optional: an account made with a phone number has none.
* `sessions`, `plans` (seeded), `subscriptions`, `user_company_access`,
  `usage_ledger`, `billing_events`, `idempotency_keys`, `rate_limits`.

Revision ID: 0018
Revises: 0017
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)

# Display prices in USD cents; what is charged is the Stripe Price's.
PLANS = [
    ("free", "Free", "Try Parallx on two companies", 0, 0, 2, 10, 0, {
        "promise_tracker": "basic", "timelines": "basic", "evidence_viewer": True,
        "weekly_alerts": False, "daily_monitoring": False, "advanced_comparisons": False,
        "exports": False, "transcripts": False}),
    ("investor", "Iix", "For investors", 1900, 19000, 10, 100, 1, {
        "promise_tracker": "full", "timelines": "full", "evidence_viewer": True,
        "weekly_alerts": True, "daily_monitoring": False, "advanced_comparisons": False,
        "exports": False, "transcripts": False}),
    ("analyst", "IIx", "For analysts", 3900, 39000, 50, 500, 2, {
        "promise_tracker": "full", "timelines": "full", "evidence_viewer": True,
        "weekly_alerts": True, "daily_monitoring": True, "advanced_comparisons": True,
        "exports": True, "transcripts": "where_licensed"}),
]


def upgrade() -> None:
    op.alter_column("users", "email", nullable=True)
    for col in (sa.Column("updated_at", TS), sa.Column("auth_uid", sa.String(128)),
                sa.Column("auth_provider", sa.String(16)),
                sa.Column("email_normalized", sa.String(255)),
                sa.Column("email_verified", sa.Boolean, nullable=False, server_default="false"),
                sa.Column("phone", sa.String(20)), sa.Column("last_login_at", TS),
                sa.Column("billing_customer_id", sa.String(255))):
        op.add_column("users", col)
    for col in ("auth_uid", "email_normalized", "phone", "billing_customer_id"):
        op.create_unique_constraint(op.f(f"uq_users_{col}"), "users", [col])

    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("idle_expires_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("user_agent", sa.String(255)),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "plans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(128), nullable=False),
        sa.Column("monthly_price", sa.Integer, nullable=False),
        sa.Column("annual_price", sa.Integer, nullable=False),
        sa.Column("max_companies", sa.Integer, nullable=False),
        sa.Column("monthly_ai_queries", sa.Integer, nullable=False),
        sa.Column("entitlements", JSONB, nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
    )
    # plain SQL, so `alembic upgrade --sql` (db/schema.sql) can render it too
    def q(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"
    for i, n, a, m, y, c, qn, o, e in PLANS:
        op.execute(
            "INSERT INTO plans (id, name, audience, monthly_price, annual_price, max_companies, "
            f"monthly_ai_queries, entitlements, sort) VALUES ({q(i)}, {q(n)}, {q(a)}, {m}, {y}, "
            f"{c}, {qn}, {q(json.dumps(e))}::jsonb, {o})")

    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("billing_provider", sa.String(16), nullable=False),
        sa.Column("provider_customer_id", sa.String(255), nullable=False),
        sa.Column("provider_subscription_id", sa.String(255), nullable=False, unique=True),
        sa.Column("provider_price_id", sa.String(255)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("billing_interval", sa.String(8), nullable=False),
        sa.Column("current_period_start", TS),
        sa.Column("current_period_end", TS),
        sa.Column("cancel_at_period_end", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("canceled_at", TS),
        sa.Column("requires_action", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("last_event_id", sa.String(255)),
        sa.Column("last_event_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('incomplete','incomplete_expired','trialing','active',"
                           "'past_due','canceled','unpaid','paused')",
                           name=op.f("ck_subscriptions_status")),
        sa.CheckConstraint("billing_interval in ('month','year')",
                           name=op.f("ck_subscriptions_billing_interval")),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    op.create_table(
        "user_company_access",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.BigInteger,
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_researched_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_accessed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("access_source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint("user_id", "company_id",
                            name=op.f("uq_user_company_access_user_id_company_id")),
        sa.CheckConstraint("status in ('pending','active','refunded')",
                           name=op.f("ck_user_company_access_status")),
        sa.CheckConstraint("access_source in ('free_credit','plan','staff')",
                           name=op.f("ck_user_company_access_access_source")),
    )
    op.create_index("ix_user_company_access_user_id_last_accessed_at", "user_company_access",
                    ["user_id", "last_accessed_at"])

    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("company_id", sa.BigInteger,
                  sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("subscription_id", UUID(as_uuid=True),
                  sa.ForeignKey("subscriptions.id", ondelete="SET NULL")),
        sa.Column("period_start", TS),
        sa.Column("period_end", TS),
        sa.Column("idempotency_key", sa.String(160), unique=True),
        sa.Column("detail", JSONB),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('free_company_credit','paid_company','ai_query',"
                           "'period_reset','refund')", name=op.f("ck_usage_ledger_kind")),
    )
    op.create_index("ix_usage_ledger_user_id_kind_created_at", "usage_ledger",
                    ["user_id", "kind", "created_at"])

    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("event_created_at", TS),
        sa.Column("processed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("outcome", sa.String(16), nullable=False),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("response", JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "scope", "key",
                            name=op.f("uq_idempotency_keys_user_id_scope_key")),
    )
    op.create_table(
        "rate_limits",
        sa.Column("bucket", sa.String(200), primary_key=True),
        sa.Column("window_start", TS, primary_key=True),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for t in ("rate_limits", "idempotency_keys", "billing_events", "usage_ledger",
              "user_company_access", "subscriptions", "plans", "sessions"):
        op.drop_table(t)
    for col in ("auth_uid", "email_normalized", "phone", "billing_customer_id"):
        op.drop_constraint(op.f(f"uq_users_{col}"), "users", type_="unique")
    for col in ("updated_at", "auth_uid", "auth_provider", "email_normalized", "email_verified",
                "phone", "last_login_at", "billing_customer_id"):
        op.drop_column("users", col)
    op.alter_column("users", "email", nullable=False)
