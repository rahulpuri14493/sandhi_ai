"""Legacy 019: add tool_visibility to jobs and workflow_steps.

Revision ID: 019_legacy_019
Revises: 018_legacy_018
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "019_legacy_019"
down_revision: Union[str, None] = "018_legacy_018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tool_visibility VARCHAR(20)")
    op.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS tool_visibility VARCHAR(20)")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS tool_visibility")
        op.execute("ALTER TABLE workflow_steps DROP COLUMN IF EXISTS tool_visibility")
