"""Legacy 003: add api_key column to agents.

Revision ID: 005_legacy_003
Revises: 004_legacy_002
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005_legacy_003"
down_revision: Union[str, None] = "004_legacy_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS api_key")
