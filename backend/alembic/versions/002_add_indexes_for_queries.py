"""Add indexes for user_id, agent_id, job_id and common query paths (issue #20).

Revision ID: 002_indexes
Revises: 001_initial
Create Date: 2026-03-15

Adds indexes on foreign keys and frequently filtered columns for marketplace,
jobs, and agent queries. All indexes use IF NOT EXISTS for idempotency.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_indexes"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the current database (PostgreSQL)."""
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t)"
        ),
        {"t": table_name},
    )
    return result.scalar()


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # Helper: only create index if the target table already exists.
    # Some tables (e.g. job_questions) are created by later legacy migrations,
    # so they may not be present when this migration runs.
    def _safe_index(sql: str, table: str) -> None:
        if _table_exists(conn, table):
            op.execute(sql)

    # Agents: developer_id (list by developer), status (marketplace filter)
    _safe_index("CREATE INDEX IF NOT EXISTS ix_agents_developer_id ON agents(developer_id)", "agents")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_agents_status ON agents(status)", "agents")
    # Jobs: business_id (list by business), status (filter)
    _safe_index("CREATE INDEX IF NOT EXISTS ix_jobs_business_id ON jobs(business_id)", "jobs")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status)", "jobs")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs(created_at)", "jobs")
    # Workflow steps: job_id, agent_id (joins and step lookup)
    _safe_index("CREATE INDEX IF NOT EXISTS ix_workflow_steps_job_id ON workflow_steps(job_id)", "workflow_steps")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_workflow_steps_agent_id ON workflow_steps(agent_id)", "workflow_steps")
    # Job questions: job_id (table created by legacy migration 007)
    _safe_index("CREATE INDEX IF NOT EXISTS ix_job_questions_job_id ON job_questions(job_id)", "job_questions")
    # Transactions: job_id (unique), payer_id
    _safe_index("CREATE INDEX IF NOT EXISTS ix_transactions_payer_id ON transactions(payer_id)", "transactions")
    # Earnings: developer_id, transaction_id
    _safe_index("CREATE INDEX IF NOT EXISTS ix_earnings_developer_id ON earnings(developer_id)", "earnings")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_earnings_transaction_id ON earnings(transaction_id)", "earnings")
    # Agent reviews: agent_id, user_id (marketplace ratings)
    _safe_index("CREATE INDEX IF NOT EXISTS ix_agent_reviews_agent_id ON agent_reviews(agent_id)", "agent_reviews")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_agent_reviews_user_id ON agent_reviews(user_id)", "agent_reviews")
    # Hiring: business_id, status
    _safe_index("CREATE INDEX IF NOT EXISTS ix_hiring_positions_business_id ON hiring_positions(business_id)", "hiring_positions")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_hiring_positions_status ON hiring_positions(status)", "hiring_positions")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_agent_nominations_hiring_position_id ON agent_nominations(hiring_position_id)", "agent_nominations")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_agent_nominations_agent_id ON agent_nominations(agent_id)", "agent_nominations")
    # Audit logs: entity lookups
    _safe_index("CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_type_id ON audit_logs(entity_type, entity_id)", "audit_logs")
    # MCP: user_id already indexed in model; ensure presence
    _safe_index("CREATE INDEX IF NOT EXISTS ix_mcp_server_connections_user_id ON mcp_server_connections(user_id)", "mcp_server_connections")
    _safe_index("CREATE INDEX IF NOT EXISTS ix_mcp_tool_configs_user_id ON mcp_tool_configs(user_id)", "mcp_tool_configs")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    indexes = [
        "ix_agents_developer_id", "ix_agents_status",
        "ix_jobs_business_id", "ix_jobs_status", "ix_jobs_created_at",
        "ix_workflow_steps_job_id", "ix_workflow_steps_agent_id",
        "ix_job_questions_job_id", "ix_transactions_payer_id",
        "ix_earnings_developer_id", "ix_earnings_transaction_id",
        "ix_agent_reviews_agent_id", "ix_agent_reviews_user_id",
        "ix_hiring_positions_business_id", "ix_hiring_positions_status",
        "ix_agent_nominations_hiring_position_id", "ix_agent_nominations_agent_id",
        "ix_audit_logs_entity_type_id",
        "ix_mcp_server_connections_user_id", "ix_mcp_tool_configs_user_id",
    ]
    for idx in indexes:
        # Drop by name; table is unknown so we use DROP INDEX CONCURRENTLY-safe form
        op.execute(f"DROP INDEX IF EXISTS {idx}")
