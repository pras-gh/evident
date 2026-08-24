"""chunk key and paragraph provenance

The first revision had a single `paragraph_id` column on `chunks`, and the
ingest worker filled it with the *chunk's* id. Paragraph-level provenance was
therefore never stored, which quietly reduced "cite the paragraph" to "cite the
chunk". This separates the two identities.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("chunks", "paragraph_id", new_column_name="chunk_key")
    op.execute("ALTER INDEX IF EXISTS uq_chunks_document_id_paragraph_id "
               "RENAME TO uq_chunks_document_id_chunk_key")
    op.add_column("chunks", sa.Column("paragraph_ids", sa.ARRAY(sa.Text()),
                                      nullable=False, server_default="{}"))
    # Existing rows have no recoverable paragraph list; seeding with the chunk
    # key keeps citations resolvable rather than leaving them empty.
    op.execute("UPDATE chunks SET paragraph_ids = ARRAY[chunk_key] "
               "WHERE paragraph_ids = '{}'")


def downgrade() -> None:
    op.drop_column("chunks", "paragraph_ids")
    op.execute("ALTER INDEX IF EXISTS uq_chunks_document_id_chunk_key "
               "RENAME TO uq_chunks_document_id_paragraph_id")
    op.alter_column("chunks", "chunk_key", new_column_name="paragraph_id")
