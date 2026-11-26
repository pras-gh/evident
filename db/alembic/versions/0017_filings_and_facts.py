"""the filing index and financial statements, from EDGAR's APIs

Every tracked company is known before any of its filings is read:

* `filings` — its 10-Ks, 10-Qs and 8-Ks, from the Submissions API, metadata
  only. A filing's text is downloaded on demand into `documents`.
* `financial_facts` — its financial statements, from the Company Facts API,
  one value per statement line per EDGAR calendar frame.
* `companies.filings_indexed_at` / `facts_loaded_at` — when each was last
  read, so a backfill knows what it has done.

Revision ID: 0017
Revises: 0016
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("filings_indexed_at", sa.DateTime(timezone=True)))
    op.add_column("companies", sa.Column("facts_loaded_at", sa.DateTime(timezone=True)))
    op.create_table(
        "filings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("company_id", sa.BigInteger,
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False, unique=True),
        sa.Column("form_type", sa.String(16), nullable=False),
        sa.Column("filed_date", sa.Date, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_date", sa.Date),
        sa.Column("primary_document", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("items", sa.String(128)),
        sa.Column("size", sa.Integer),
        sa.Column("is_xbrl", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("alerted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_filings_company_id_accepted_at", "filings",
                    ["company_id", "accepted_at"])
    op.create_index("ix_filings_accepted_at", "filings", ["accepted_at"])
    op.create_table(
        "financial_facts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("company_id", sa.BigInteger,
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("concept", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(28, 6), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("filed", sa.Date, nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger),
        sa.Column("fiscal_period", sa.String(4)),
        sa.Column("frame", sa.String(16), nullable=False),
        sa.UniqueConstraint("company_id", "concept", "unit", "frame",
                            name=op.f("uq_financial_facts_company_id_concept_unit_frame")),
    )
    op.create_index("ix_financial_facts_company_id_period_end", "financial_facts",
                    ["company_id", "period_end"])


def downgrade() -> None:
    op.drop_table("financial_facts")
    op.drop_table("filings")
    op.drop_column("companies", "facts_loaded_at")
    op.drop_column("companies", "filings_indexed_at")
