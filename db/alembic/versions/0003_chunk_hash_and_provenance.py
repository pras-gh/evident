"""chunk_hash and provenance on every extracted object

Two changes.

`chunks.chunk_key` becomes `chunks.chunk_hash`, derived from
company_id + document_accession + page_number + normalised text, and unique
across the whole table rather than per document.

Including the page in the key is load-bearing. Measured on NVDA's FY2025 10-K:
without it, 36 texts repeat across the document and "table of contents" alone
appears 82 times, all of which would collapse into a single row. With the page
included, exactly one same-page duplicate collides out of 1,156 paragraphs.

Every extracted object also gains page_number, paragraph_id and confidence, so
a topic, risk, person, metric observation or timeline event can say exactly
where it came from.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVENANCE_TABLES = ("topic_mentions", "risks", "people",
                      "metric_observations", "timeline_events")


def upgrade() -> None:
    op.alter_column("chunks", "chunk_key", new_column_name="chunk_hash",
                    type_=sa.String(64), existing_type=sa.String(64))
    # The index backs a UNIQUE constraint, so it has to go via ALTER TABLE —
    # DROP INDEX is refused while the constraint depends on it.
    op.execute("ALTER TABLE chunks DROP CONSTRAINT IF EXISTS "
               "uq_chunks_document_id_chunk_key")
    # Existing rows carry per-document keys that are not the new derivation.
    # Re-ingest rebuilds them; the constraint is created NOT VALID-free here
    # because a partially migrated table would otherwise refuse the index.
    op.execute("DELETE FROM chunks a USING chunks b "
               "WHERE a.id > b.id AND a.chunk_hash = b.chunk_hash")
    op.create_unique_constraint("uq_chunks_chunk_hash", "chunks", ["chunk_hash"])

    for table in _PROVENANCE_TABLES:
        op.add_column(table, sa.Column("page_number", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("paragraph_id", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("confidence", sa.Float(), nullable=True))
        op.create_index(f"ix_{table}_paragraph_id", table, ["paragraph_id"])


def downgrade() -> None:
    for table in _PROVENANCE_TABLES:
        op.drop_index(f"ix_{table}_paragraph_id", table_name=table)
        op.drop_column(table, "confidence")
        op.drop_column(table, "paragraph_id")
        op.drop_column(table, "page_number")

    op.drop_constraint("uq_chunks_chunk_hash", "chunks", type_="unique")
    op.alter_column("chunks", "chunk_hash", new_column_name="chunk_key",
                    type_=sa.String(64), existing_type=sa.String(64))
    op.create_unique_constraint("uq_chunks_document_id_chunk_key", "chunks",
                                ["document_id", "chunk_key"])
