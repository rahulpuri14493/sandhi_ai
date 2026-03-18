"""Legacy 016: add allowed_platform_tool_ids, allowed_connection_ids to jobs and workflow_steps.

Revision ID: 016_legacy_016
Revises: 015_legacy_015
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "016_legacy_016"
down_revision: Union[str, None] = "015_legacy_015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS allowed_platform_tool_ids TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS allowed_connection_ids TEXT")
    op.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS allowed_platform_tool_ids TEXT")
    op.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS allowed_connection_ids TEXT")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS allowed_platform_tool_ids")
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS allowed_connection_ids")
        op.execute("ALTER TABLE workflow_steps DROP COLUMN IF EXISTS allowed_platform_tool_ids")
        op.execute("ALTER TABLE workflow_steps DROP COLUMN IF EXISTS allowed_connection_ids")
