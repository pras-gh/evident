"""on-demand company fetches

A search naming a company with no filings here fetches its latest annual report
from EDGAR (`workers/fetch_worker.py`). One row per fetch: queued, running, done
or failed, and at most one under way per company, enforced by a partial unique
index so two searches at once start one fetch.

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_fetches",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("ticker", sa.String(16)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text),
        sa.Column("accession", sa.String(32)),
        sa.Column("form_type", sa.String(16)),
        sa.Column("chunks", sa.Integer),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status in ('queued','running','done','failed')",
                           name="ck_company_fetches_status"),
    )
    op.create_index("uq_company_fetches_active_cik", "company_fetches", ["cik"],
                    unique=True, postgresql_where=sa.text("status in ('queued','running')"))
    op.create_index("ix_company_fetches_cik_requested_at", "company_fetches",
                    ["cik", "requested_at"])


def downgrade() -> None:
    op.drop_table("company_fetches")
