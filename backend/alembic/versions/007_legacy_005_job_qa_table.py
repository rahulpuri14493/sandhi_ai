"""Legacy 005: create job_questions table.

Revision ID: 007_legacy_005
Revises: 006_legacy_004
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "007_legacy_005"
down_revision: Union[str, None] = "006_legacy_004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_questions (
            id SERIAL PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            answered_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_questions_job_id ON job_questions(job_id)")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS job_questions")
