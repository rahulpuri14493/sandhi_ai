"""Legacy 004: create hiring_positions and agent_nominations tables.

Revision ID: 006_legacy_004
Revises: 005_legacy_003
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "006_legacy_004"
down_revision: Union[str, None] = "005_legacy_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("DO $$ BEGIN CREATE TYPE hiringstatus AS ENUM ('open', 'closed', 'filled'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE nominationstatus AS ENUM ('pending', 'approved', 'rejected'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("""
        CREATE TABLE IF NOT EXISTS hiring_positions (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES users(id),
            title VARCHAR NOT NULL,
            description TEXT,
            requirements TEXT,
            status hiringstatus DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_nominations (
            id SERIAL PRIMARY KEY,
            hiring_position_id INTEGER NOT NULL REFERENCES hiring_positions(id) ON DELETE CASCADE,
            agent_id INTEGER NOT NULL REFERENCES agents(id),
            developer_id INTEGER NOT NULL REFERENCES users(id),
            cover_letter TEXT,
            status nominationstatus DEFAULT 'pending',
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TIMESTAMP,
            review_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_hiring_positions_business_id ON hiring_positions(business_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_hiring_positions_status ON hiring_positions(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_nominations_position_id ON agent_nominations(hiring_position_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_nominations_agent_id ON agent_nominations(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_nominations_developer_id ON agent_nominations(developer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_nominations_status ON agent_nominations(status)")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS agent_nominations")
    op.execute("DROP TABLE IF EXISTS hiring_positions")
    op.execute("DROP TYPE IF EXISTS nominationstatus")
    op.execute("DROP TYPE IF EXISTS hiringstatus")
