"""Add indexes for user_id, agent_id, job_id and common query paths (issue #20).

Revision ID: 002_indexes
Revises: 001_initial
Create Date: 2026-03-15

Adds indexes on foreign keys and frequently filtered columns for marketplace,
jobs, and agent queries. All indexes use IF NOT EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002_indexes"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    # Agents: developer_id (list by developer), status (marketplace filter)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agents_developer_id ON agents(developer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agents_status ON agents(status)")
    # Jobs: business_id (list by business), status (filter)
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_business_id ON jobs(business_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs(created_at)")
    # Workflow steps: job_id, agent_id (joins and step lookup)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_steps_job_id ON workflow_steps(job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_steps_agent_id ON workflow_steps(agent_id)")
    # Job questions: job_id
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_questions_job_id ON job_questions(job_id)")
    # Transactions: job_id (unique), payer_id
    op.execute("CREATE INDEX IF NOT EXISTS ix_transactions_payer_id ON transactions(payer_id)")
    # Earnings: developer_id, transaction_id
    op.execute("CREATE INDEX IF NOT EXISTS ix_earnings_developer_id ON earnings(developer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_earnings_transaction_id ON earnings(transaction_id)")
    # Agent reviews: agent_id, user_id (marketplace ratings)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_reviews_agent_id ON agent_reviews(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_reviews_user_id ON agent_reviews(user_id)")
    # Hiring: business_id, status
    op.execute("CREATE INDEX IF NOT EXISTS ix_hiring_positions_business_id ON hiring_positions(business_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_hiring_positions_status ON hiring_positions(status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_nominations_hiring_position_id ON agent_nominations(hiring_position_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_nominations_agent_id ON agent_nominations(agent_id)")
    # Audit logs: entity lookups
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_type_id ON audit_logs(entity_type, entity_id)")
    # MCP: user_id already indexed in model; ensure presence
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_server_connections_user_id ON mcp_server_connections(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_tool_configs_user_id ON mcp_tool_configs(user_id)")


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
