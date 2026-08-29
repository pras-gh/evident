"""narrow chunks.embedding to the width every provider can emit

The column was `vector(1536)`. No Voyage model emits 1536 — `voyage-finance-2`
is 1024-only and the general models offer 256/512/1024/2048 — so with the column
at 1536, "switch the embedding provider" was a schema migration rather than a
configuration change.

1024 is the intersection: `voyage-finance-2` emits it natively, Voyage's general
models can be asked for it, and OpenAI's `text-embedding-3-large` shortens to it
via `dimensions`. Pinning the column there is what makes the provider swap a
one-line change.

**Existing vectors are discarded, not converted.** A 1536-wide vector cannot be
narrowed to a meaningful 1024-wide one — truncating it produces a number that
still indexes and no longer means anything. Every vector currently stored came
from `HashingEmbedder`, which has no semantics either, so nothing of value is
lost; but the same reasoning would apply to real vectors, and the answer would
still be "re-embed", never "reshape".

`embedding_provider` and `embedding_model` are cleared alongside, because a row
claiming to be a Voyage vector while holding nothing is worse than a null.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_DIM = 1024
OLD_DIM = 1536


def upgrade() -> None:
    # The HNSW index is built for the old width and cannot survive the change.
    op.drop_index("ix_chunks_embedding", table_name="chunks")

    # Cleared before the type change: ALTER TYPE would have to reinterpret
    # every existing vector, and there is no correct reinterpretation.
    op.execute("update chunks set embedding = null, "
               "embedding_provider = null, embedding_model = null "
               "where embedding is not null")

    op.alter_column("chunks", "embedding",
                    type_=pgvector.sqlalchemy.VECTOR(NEW_DIM),
                    existing_type=pgvector.sqlalchemy.VECTOR(OLD_DIM),
                    existing_nullable=True,
                    postgresql_using="null")

    op.create_index("ix_chunks_embedding", "chunks", ["embedding"],
                    postgresql_using="hnsw",
                    postgresql_with={"m": 16, "ef_construction": 64},
                    postgresql_ops={"embedding": "vector_cosine_ops"})


def downgrade() -> None:
    # Widening back is mechanical; the vectors are not recoverable either way,
    # so this restores the shape and leaves the column empty.
    op.drop_index("ix_chunks_embedding", table_name="chunks")
    op.execute("update chunks set embedding = null, "
               "embedding_provider = null, embedding_model = null "
               "where embedding is not null")
    op.alter_column("chunks", "embedding",
                    type_=pgvector.sqlalchemy.VECTOR(OLD_DIM),
                    existing_type=pgvector.sqlalchemy.VECTOR(NEW_DIM),
                    existing_nullable=True,
                    postgresql_using="null")
    op.create_index("ix_chunks_embedding", "chunks", ["embedding"],
                    postgresql_using="hnsw",
                    postgresql_with={"m": 16, "ef_construction": 64},
                    postgresql_ops={"embedding": "vector_cosine_ops"})
