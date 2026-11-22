"""the internal organization's slug is parallx-internal

0014 renamed the internal organization and kept its slug. The code now calls
it `parallx-internal` (`INTERNAL_ORG_SLUG`), so the row follows: the gateway
and `tools/admin.py` find the organization by that slug, and with the old one
in place they would make a second internal organization rather than find the
first, and its spend history with it.

Revision ID: 0015
Revises: 0014
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE organizations SET slug = 'parallx-internal' "
               "WHERE slug = 'evident-internal'")


def downgrade() -> None:
    op.execute("UPDATE organizations SET slug = 'evident-internal' "
               "WHERE slug = 'parallx-internal'")
