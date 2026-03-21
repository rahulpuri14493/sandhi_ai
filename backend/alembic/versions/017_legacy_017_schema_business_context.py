"""Legacy 017: add schema_metadata, business_description to mcp_tool_configs.

Revision ID: 017_legacy_017
Revises: 016_legacy_016
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "017_legacy_017"
down_revision: Union[str, None] = "016_legacy_016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("DO $$ BEGIN ALTER TABLE mcp_tool_configs ADD COLUMN schema_metadata TEXT; EXCEPTION WHEN duplicate_column THEN null; END $$;")
    op.execute("DO $$ BEGIN ALTER TABLE mcp_tool_configs ADD COLUMN business_description VARCHAR(2000); EXCEPTION WHEN duplicate_column THEN null; END $$;")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE mcp_tool_configs DROP COLUMN IF EXISTS schema_metadata")
        op.execute("ALTER TABLE mcp_tool_configs DROP COLUMN IF EXISTS business_description")
