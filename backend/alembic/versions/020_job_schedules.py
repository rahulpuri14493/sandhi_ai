"""Create job_schedules table for one-time job scheduling.

Revision ID: 020_job_schedules
Revises: 019_legacy_019
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "020_job_schedules"
down_revision: Union[str, None] = "019_legacy_019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE schedulestatus AS ENUM ('active','inactive'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    # Add IN_QUEUE to jobstatus enum (required for scheduled jobs).
    op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'IN_QUEUE' AFTER 'APPROVED'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_schedules (
            id SERIAL PRIMARY KEY,
            job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
            status schedulestatus NOT NULL DEFAULT 'active',
            timezone VARCHAR NOT NULL DEFAULT 'UTC',
            scheduled_at TIMESTAMP NOT NULL,
            last_run_time TIMESTAMP,
            next_run_time TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    # Clean up columns from an earlier prototype that supported cron/recurring
    # schedules.  Safe no-op when columns do not exist (IF EXISTS).
    op.execute("ALTER TABLE job_schedules DROP COLUMN IF EXISTS cron_expression")
    op.execute("ALTER TABLE job_schedules DROP COLUMN IF EXISTS is_one_time")
    op.execute("ALTER TABLE job_schedules DROP COLUMN IF EXISTS days_of_week")
    op.execute("ALTER TABLE job_schedules DROP COLUMN IF EXISTS schedule_time")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_schedules_next_run_time "
        "ON job_schedules(next_run_time) WHERE next_run_time IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_schedules_status ON job_schedules(status)")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS job_schedules")
        op.execute("DROP TYPE IF EXISTS schedulestatus")
