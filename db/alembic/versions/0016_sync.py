"""S&P 500 sync: where a fetch came from, and each check of EDGAR

The sync worker fills in every S&P 500 company's latest annual report
(`backfill`) and fetches new 10-Ks and 10-Qs from them as EDGAR accepts them
(`watch`), through the same `company_fetches` rows a search makes. `source`
tells them apart. `sync_runs` records each check of EDGAR: when it ran, what
it saw, and what it fetched, which is how the worker knows how long it was
away and how the status page knows how current Parallx is.

Revision ID: 0016
Revises: 0015
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("company_fetches", sa.Column(
        "source", sa.String(16), nullable=False, server_default="search"))
    # op.f: the name as written; the naming convention would prefix it again
    op.create_check_constraint(op.f("ck_company_fetches_source"), "company_fetches",
                               "source in ('search','backfill','watch')")
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("seen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("new", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.CheckConstraint("kind in ('poll','sweep')", name=op.f("ck_sync_runs_kind")),
    )
    op.create_index("ix_sync_runs_kind_started_at", "sync_runs", ["kind", "started_at"])


def downgrade() -> None:
    op.drop_table("sync_runs")
    op.drop_constraint(op.f("ck_company_fetches_source"), "company_fetches", type_="check")
    op.drop_column("company_fetches", "source")
