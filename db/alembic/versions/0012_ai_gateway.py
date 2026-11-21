"""identity, AI usage logs and the AI response cache

Every AI request now goes through one gateway, and every one is attributed to
an organization (and, when a person made it, a user), costed and logged. There
was no identity of any kind before this, so it adds the minimum: organizations,
users with a role, and API keys stored as SHA-256 hashes.

* `ai_usage_logs` — one row per provider attempt, plus one per request served
  from cache or refused by a usage limit. No prompt or filing text, ever.
* `ai_response_cache` — finished analyses keyed by accession, task, provider,
  model, prompt version and input hash.
* the internal organization background jobs run as, so their spend is capped
  and visible like a customer's.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _now():
    return sa.Column("created_at", sa.DateTime(timezone=True),
                     server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("daily_requests_per_user", sa.Integer()),
        sa.Column("monthly_tokens", sa.BigInteger()),
        sa.Column("monthly_spend_usd", sa.Numeric(12, 2)),
        sa.Column("max_input_tokens", sa.Integer()),
        sa.Column("max_output_tokens", sa.Integer()),
        _now(),
    )
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID,
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255)),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        _now(),
        sa.CheckConstraint("role in ('member','org_admin','platform_admin')", name="role"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_table(
        "api_keys",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255)),
        _now(),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "ai_usage_logs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("organization_id", UUID,
                  sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text()),
        _now(),
        sa.CheckConstraint(
            "status in ('success','failed','fallback','cached','blocked')", name="status"),
    )
    op.create_index("ix_ai_usage_logs_organization_id_created_at", "ai_usage_logs",
                    ["organization_id", "created_at"])
    op.create_index("ix_ai_usage_logs_user_id_created_at", "ai_usage_logs",
                    ["user_id", "created_at"])
    op.create_index("ix_ai_usage_logs_created_at", "ai_usage_logs", ["created_at"])
    op.create_index("ix_ai_usage_logs_request_id", "ai_usage_logs", ["request_id"])
    op.create_table(
        "ai_response_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("stop_reason", sa.String(16), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        _now(),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_hit_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index("ix_ai_response_cache_accession", "ai_response_cache", ["accession"])

    # The organization background jobs run as.
    op.execute("INSERT INTO organizations (id, slug, name, is_internal) "
               "VALUES (gen_random_uuid(), 'evident-internal', 'Evident (internal)', true)")


def downgrade() -> None:
    op.drop_index("ix_ai_response_cache_accession", table_name="ai_response_cache")
    op.drop_table("ai_response_cache")
    for ix in ("ix_ai_usage_logs_request_id", "ix_ai_usage_logs_created_at",
               "ix_ai_usage_logs_user_id_created_at",
               "ix_ai_usage_logs_organization_id_created_at"):
        op.drop_index(ix, table_name="ai_usage_logs")
    op.drop_table("ai_usage_logs")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
