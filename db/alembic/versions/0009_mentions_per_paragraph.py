"""one mention per entity per paragraph, not per chunk

`entity_mentions` was unique on (entity_id, chunk_id). That was harmless while
every citation in a chunk pointed at the chunk's first paragraph — there was
only ever one paragraph to cite. Once extraction started citing the paragraph
that actually supports an entity, the constraint kept whichever paragraph
arrived first and silently dropped every other citation of that entity in the
chunk. In the benchmark corpus, export controls is named in ten paragraphs and
four were stored; a subsection heading was kept and the paragraphs describing
the licensing requirements were discarded.

The key is now (entity_id, chunk_id, paragraph_id) with NULLS NOT DISTINCT,
so a citation with no paragraph — a table — is still one row, and re-running
extraction still cannot inflate a count. That needs PostgreSQL 15+.

`mention_count` now counts paragraphs, which is what the extraction prompt has
always asked for: an entity repeated once per supporting paragraph, because
repetition is how importance is measured. Existing counts are not recomputed
here; they were counting chunks and are corrected the next time a document is
re-extracted.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_entity_mentions_entity_id_chunk_id", "entity_mentions",
                       type_="unique")
    op.create_unique_constraint(
        "uq_entity_mentions_entity_id_chunk_id_paragraph_id", "entity_mentions",
        ["entity_id", "chunk_id", "paragraph_id"],
        postgresql_nulls_not_distinct=True)


def downgrade() -> None:
    raise NotImplementedError(
        "0009 allows several mentions of one entity in one chunk. Narrowing the "
        "key back to (entity_id, chunk_id) would have to delete all but one of "
        "them, and nothing records which one the old constraint would have "
        "kept. Restore from a backup taken before the upgrade.")
