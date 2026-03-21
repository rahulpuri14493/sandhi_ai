"""Legacy 012: add depends_on_previous to workflow_steps.

Revision ID: 012_legacy_012
Revises: 011_legacy_011
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "012_legacy_012"
down_revision: Union[str, None] = "011_legacy_011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS depends_on_previous BOOLEAN NOT NULL DEFAULT true")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE workflow_steps DROP COLUMN IF EXISTS depends_on_previous")
