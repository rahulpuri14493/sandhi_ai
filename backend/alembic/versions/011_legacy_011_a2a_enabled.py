"""Legacy 011: add a2a_enabled column to agents.

Revision ID: 011_legacy_011
Revises: 010_legacy_010
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "011_legacy_011"
down_revision: Union[str, None] = "010_legacy_010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS a2a_enabled BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS a2a_enabled")
