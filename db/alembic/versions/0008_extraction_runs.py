"""extraction_runs — one row per run, with what it cost

The row a benchmark is compared against. `extraction_calls` (0007) holds the
per-request detail including raw responses; this holds the totals, so "what did
run X cost and how much was accepted" is one read rather than an aggregate over
fourteen rows.

Two deliberate departures from the shape as specified.

**`document_id` is BIGINT, not UUID.** Every primary key in this schema is
BIGINT — `documents.id` included — so a UUID column could not reference it. The
alternative is retyping every key in every table, which is a far larger change
than a benchmark table justifies. The run's own `id` *is* a UUID, which is
where it matters: the caller generates it before the first request so calls can
reference their run while the run is still in flight.

**`prompt_id` is added.** A rate change with no prompt change and no model
change is a signal about the model; one where the prompt moved is a signal
about the prompt. Recording only the model cannot tell those apart, and telling
them apart is the entire purpose of keeping the numbers.

`cost_usd` is NUMERIC because it is money. Binary floating point produces
totals that do not add up, and a benchmark whose costs disagree with the
invoice is worse than one with no costs.

`finished_at` is nullable: the row is written when the run starts, so a run
that crashes leaves evidence instead of vanishing.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_id", sa.String(64), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("chunks_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE",
                                name="fk_extraction_runs_document_id_documents"),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_runs"),
        sa.CheckConstraint("chunks_accepted + chunks_rejected <= chunks_processed",
                           name="ck_extraction_runs_chunk_counts"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_extraction_runs_cost_usd"),
    )
    op.create_index("ix_extraction_runs_document_id", "extraction_runs",
                    ["document_id"])
    op.create_index("ix_extraction_runs_document_id_started_at", "extraction_runs",
                    ["document_id", "started_at"])

    # Now that runs exist, calls can point at them. Done here rather than in
    # 0007 because the table it references did not exist yet.
    op.create_foreign_key("fk_extraction_calls_run_id_extraction_runs",
                          "extraction_calls", "extraction_runs",
                          ["run_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("fk_extraction_calls_run_id_extraction_runs",
                       "extraction_calls", type_="foreignkey")
    op.drop_table("extraction_runs")
