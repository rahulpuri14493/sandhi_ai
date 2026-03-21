"""
Add execution_token column to jobs for execution lease.
"""

from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "024_job_execution_token"
down_revision: Union[str, None] = "023_mcp_write_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS execution_token VARCHAR(64) NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_execution_token ON jobs(execution_token)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_execution_token")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS execution_token")
