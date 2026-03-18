"""Legacy 010: allow multiple reviews per user per agent.

Revision ID: 010_legacy_010
Revises: 009_legacy_009
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "010_legacy_010"
down_revision: Union[str, None] = "009_legacy_009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_reviews DROP CONSTRAINT IF EXISTS uq_agent_review_user")


def downgrade() -> None:
    pass  # Constraint re-add is optional
