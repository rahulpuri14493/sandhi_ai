"""Legacy 018: add pageindex to mcptooltype enum.

Revision ID: 018_legacy_018
Revises: 017_legacy_017
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "018_legacy_018"
down_revision: Union[str, None] = "017_legacy_017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'pageindex'; EXCEPTION WHEN duplicate_object THEN null; END $$;")


def downgrade() -> None:
    pass

