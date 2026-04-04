"""
job_planner_artifacts: Postgres pointers to planner/BRD analysis JSON in object storage.
"""

from typing import Union

from alembic import op

revision: str = "026_job_planner_artifacts"
down_revision: Union[str, None] = "024_job_execution_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_planner_artifacts (
            id SERIAL PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            artifact_type VARCHAR(64) NOT NULL,
            storage VARCHAR(16) NOT NULL DEFAULT 's3',
            bucket VARCHAR(255),
            object_key TEXT NOT NULL,
            byte_size INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_planner_artifacts_job_id ON job_planner_artifacts(job_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_job_planner_artifacts_job_id")
    op.execute("DROP TABLE IF EXISTS job_planner_artifacts")
