"""Add teams and smtp to mcptooltype enum.

Revision ID: 029_mcp_teams_smtp
Revises: 028_workflow_step_runtime_tel
Create Date: 2026-04-10
"""
from typing import Sequence, Union

from alembic import op

revision: str = "029_mcp_teams_smtp"
down_revision: Union[str, None] = "028_workflow_step_runtime_tel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALUES = ("teams", "smtp")


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    for v in _VALUES:
        op.execute(
            f"DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE '{v}'; "
            "EXCEPTION WHEN duplicate_object THEN null; END $$;"
        )


def downgrade() -> None:
    pass
