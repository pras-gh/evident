"""paragraph bounding boxes on chunks

Where each paragraph sits on its page — {page, x0, y0, x1, y1, page_width,
page_height} in points from the top-left, keyed by paragraph id — so a viewer
rendering the page can draw the highlight over the exact paragraph.

Only PDF filings have geometry. HTML filings get null: a browser decides where
their text lands, and a box invented here would look exact and be wrong.

Nullable and unindexed: it is read with the chunk, never searched. Existing
rows stay null until their filing is re-ingested.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("paragraph_boxes", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "paragraph_boxes")
