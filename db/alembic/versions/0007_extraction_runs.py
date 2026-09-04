"""extraction_runs — one row per Claude call, response kept verbatim

The benchmark table. Every request is recorded with the exact text that came
back, the prompt version and model that produced it, what it cost, and how many
items survived validation.

Storing the raw response is the point. Without it a schema change cannot be
replayed offline, a change in drop rate cannot be attributed to the prompt or
the model, and a wrong entity in the graph cannot be traced to whether the
model said something wrong or we stored it wrong.

Rejected responses are stored as well. A response that failed to parse is the
most useful artefact available when working out why it failed.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt_id", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("stop_reason", sa.String(16), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_created_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("entities_returned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entities_kept", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relationships_returned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relationships_kept", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE",
                                name="fk_extraction_runs_company_id_companies"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE",
                                name="fk_extraction_runs_document_id_documents"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="SET NULL",
                                name="fk_extraction_runs_chunk_id_chunks"),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_runs"),
        sa.UniqueConstraint("run_id", "chunk_id",
                            name="uq_extraction_runs_run_id_chunk_id"),
        sa.CheckConstraint("status in ('accepted','rejected')",
                           name="ck_extraction_runs_status"),
    )
    op.create_index("ix_extraction_runs_run_id", "extraction_runs", ["run_id"])
    op.create_index("ix_extraction_runs_company_id", "extraction_runs", ["company_id"])
    op.create_index("ix_extraction_runs_document_id", "extraction_runs", ["document_id"])
    op.create_index("ix_extraction_runs_chunk_id", "extraction_runs", ["chunk_id"])
    op.create_index("ix_extraction_runs_run_id_status", "extraction_runs",
                    ["run_id", "status"])


def downgrade() -> None:
    op.drop_table("extraction_runs")
