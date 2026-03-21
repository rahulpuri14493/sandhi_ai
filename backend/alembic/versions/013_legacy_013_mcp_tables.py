"""Legacy 013: create MCP tables (mcp_server_connections, mcp_tool_configs).

Revision ID: 013_legacy_013
Revises: 012_legacy_012
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "013_legacy_013"
down_revision: Union[str, None] = "012_legacy_012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("DO $$ BEGIN CREATE TYPE mcptooltype AS ENUM ('vector_db', 'postgres', 'filesystem'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("""
        CREATE TABLE IF NOT EXISTS mcp_server_connections (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            base_url VARCHAR(2048) NOT NULL,
            endpoint_path VARCHAR(255) NOT NULL DEFAULT '/mcp',
            auth_type VARCHAR(32) NOT NULL DEFAULT 'none',
            encrypted_credentials TEXT,
            is_platform_configured BOOLEAN NOT NULL DEFAULT false,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS mcp_tool_configs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tool_type mcptooltype NOT NULL,
            name VARCHAR(255) NOT NULL,
            encrypted_config TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcp_server_connections_user_id ON mcp_server_connections(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcp_tool_configs_user_id ON mcp_tool_configs(user_id)")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS mcp_tool_configs")
    op.execute("DROP TABLE IF EXISTS mcp_server_connections")
    op.execute("DROP TYPE IF EXISTS mcptooltype CASCADE")
