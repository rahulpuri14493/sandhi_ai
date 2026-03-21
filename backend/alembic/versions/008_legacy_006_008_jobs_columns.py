"""Legacy 006-008: add conversation, files, failure_reason to jobs.

Revision ID: 008_legacy_006
Revises: 007_legacy_005
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "008_legacy_006"
down_revision: Union[str, None] = "007_legacy_005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS conversation TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS files TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS conversation")
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS files")
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS failure_reason")
