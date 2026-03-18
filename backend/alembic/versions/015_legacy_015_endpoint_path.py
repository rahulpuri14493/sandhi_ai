"""Legacy 015: add endpoint_path to mcp_server_connections.

Revision ID: 015_legacy_015
Revises: 014_legacy_014
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "015_legacy_015"
down_revision: Union[str, None] = "014_legacy_014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcp_server_connections ADD COLUMN IF NOT EXISTS endpoint_path VARCHAR(255) NOT NULL DEFAULT '/mcp'")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE mcp_server_connections DROP COLUMN IF EXISTS endpoint_path")
