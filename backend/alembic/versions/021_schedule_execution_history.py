"""Create schedule_execution_history table for audit logging.

Revision ID: 021_exec_history
Revises: 020_job_schedules
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "021_exec_history"
down_revision: Union[str, None] = "020_job_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS schedule_execution_history (
            id SERIAL PRIMARY KEY,
            schedule_id INTEGER NOT NULL REFERENCES job_schedules(id) ON DELETE CASCADE,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMP,
            status VARCHAR(20) NOT NULL DEFAULT 'started',
            failure_reason TEXT,
            triggered_by VARCHAR(50) DEFAULT 'scheduler'
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_history_schedule_id "
        "ON schedule_execution_history(schedule_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_history_job_id "
        "ON schedule_execution_history(job_id)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS schedule_execution_history")
