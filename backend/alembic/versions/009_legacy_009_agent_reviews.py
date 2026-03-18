"""Legacy 009: create agent_reviews table.

Revision ID: 009_legacy_009
Revises: 008_legacy_006
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "009_legacy_009"
down_revision: Union[str, None] = "008_legacy_006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_reviews (
            id SERIAL PRIMARY KEY,
            agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating DOUBLE PRECISION NOT NULL,
            review_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_reviews_agent_id ON agent_reviews(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_reviews_id ON agent_reviews(id)")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS agent_reviews")
