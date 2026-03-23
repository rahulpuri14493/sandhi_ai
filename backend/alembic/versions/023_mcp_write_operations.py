"""Create MCP write operations ledger table.

Revision ID: 023_mcp_write_operations
Revises: 022_job_output_contract
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "023_mcp_write_operations"
down_revision: Union[str, None] = "022_job_output_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_write_operations (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            operation_id VARCHAR(64) NOT NULL UNIQUE,
            idempotency_key VARCHAR(255) NOT NULL,
            tool_name VARCHAR(255) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'accepted',
            request_payload TEXT NOT NULL,
            response_payload TEXT NULL,
            error_message TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            started_at TIMESTAMP NULL,
            completed_at TIMESTAMP NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_write_ops_user_idempotency "
        "ON mcp_write_operations(user_id, idempotency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_write_ops_operation_id "
        "ON mcp_write_operations(operation_id)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS mcp_write_operations")
