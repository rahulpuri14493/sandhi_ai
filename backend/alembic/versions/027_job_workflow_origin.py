"""
Track how the workflow was built (auto-split vs manual) for execute-time behavior.

manual: skip platform planner replan at execution so user-authored steps are preserved.
"""

from typing import Set, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027_job_workflow_origin"
down_revision: Union[str, None] = "026_job_planner_artifacts"
branch_labels = None
depends_on = None


def _jobs_column_names(connection) -> Set[str]:
    insp = sa.inspect(connection)
    return {c["name"] for c in insp.get_columns("jobs")}


def upgrade() -> None:
    conn = op.get_bind()
    if "workflow_origin" in _jobs_column_names(conn):
        return
    op.add_column(
        "jobs",
        sa.Column(
            "workflow_origin",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'auto_split'"),
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if "workflow_origin" not in _jobs_column_names(conn):
        return
    op.drop_column("jobs", "workflow_origin")
