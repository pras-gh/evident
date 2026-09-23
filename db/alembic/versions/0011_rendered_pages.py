"""rendered pages, filing files, highlight colours

What the evidence viewer needs to show a filing as pages and draw a citation
over the paragraph it names:

* `documents.source_path` — the filing's bytes as fetched, so it can be
  rendered again without going back to EDGAR
* `documents.pdf_path` — a PDF of the filing (the source for PDF filings,
  Chrome's print of it for HTML ones)
* `documents.rendered_at` — when page images and boxes were last made
* `document_pages` — one row per page: size in points, image, thumbnail
* `entities.highlight_color` — `#rrggbb` an entity's citations are drawn in;
  null means its type's colour

Paragraph boxes stay in `chunks.paragraph_boxes` (0010): the render worker
fills them for HTML filings, measured on the same rendering the images show.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_path", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("pdf_path", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("rendered_at", sa.DateTime(timezone=True),
                                         nullable=True))
    op.add_column("entities", sa.Column("highlight_color", sa.String(7), nullable=True))
    op.create_check_constraint("highlight_color", "entities",
                               "highlight_color ~ '^#[0-9a-fA-F]{6}$'")
    op.create_table(
        "document_pages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("document_id", sa.BigInteger(),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("paragraph_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "page"),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_pages_document_id", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_constraint("highlight_color", "entities", type_="check")
    op.drop_column("entities", "highlight_color")
    op.drop_column("documents", "rendered_at")
    op.drop_column("documents", "pdf_path")
    op.drop_column("documents", "source_path")
