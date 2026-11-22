"""the internal organization is Parallx's

The company is now Parallx (ll.ax). The internal organization's display name,
which the AI usage dashboard shows, follows; its slug is an identifier and
stays.

Revision ID: 0014
Revises: 0013
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE organizations SET name = 'Parallx (internal)' "
               "WHERE slug = 'evident-internal' AND name = 'Evident (internal)'")


def downgrade() -> None:
    op.execute("UPDATE organizations SET name = 'Evident (internal)' "
               "WHERE slug = 'evident-internal' AND name = 'Parallx (internal)'")
