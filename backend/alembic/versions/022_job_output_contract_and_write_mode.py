"""Add job output contract and write execution mode fields.

Revision ID: 022_job_output_contract
Revises: 021_exec_history
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022_job_output_contract"
down_revision: Union[str, None] = "021_exec_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS write_execution_mode VARCHAR(20) NOT NULL DEFAULT 'platform'")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS output_artifact_format VARCHAR(20) NOT NULL DEFAULT 'jsonl'")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS output_contract TEXT NULL")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS output_contract")
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS output_artifact_format")
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS write_execution_mode")
