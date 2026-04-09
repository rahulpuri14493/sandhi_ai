"""
Durable workflow-step runtime telemetry snapshot fields.

These columns support production-safe heartbeat diagnostics:
- real-time live state in Redis
- durable fallback reason/timestamps in Postgres
"""

from typing import Set, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028_workflow_step_runtime_tel"
down_revision: Union[str, None] = "027_job_workflow_origin"
branch_labels = None
depends_on = None


def _workflow_steps_column_names(connection) -> Set[str]:
    insp = sa.inspect(connection)
    return {c["name"] for c in insp.get_columns("workflow_steps")}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _workflow_steps_column_names(conn)

    additions = [
        ("last_progress_at", sa.DateTime(), True),
        ("last_activity_at", sa.DateTime(), True),
        ("live_phase", sa.String(length=32), True),
        ("live_phase_started_at", sa.DateTime(), True),
        ("live_reason_code", sa.String(length=64), True),
        ("live_reason_detail", sa.Text(), True),
        ("live_trace_id", sa.String(length=64), True),
        ("live_attempt", sa.Integer(), True),
        ("stuck_since", sa.DateTime(), True),
        ("stuck_reason", sa.String(length=128), True),
    ]
    for name, col_type, nullable in additions:
        if name not in existing:
            op.add_column("workflow_steps", sa.Column(name, col_type, nullable=nullable))

    # Watchdog/status query helpers.
    idx_last_progress = "ix_workflow_steps_status_last_progress_at"
    idx_live_phase = "ix_workflow_steps_live_phase_started_at"
    if idx_last_progress not in {i["name"] for i in sa.inspect(conn).get_indexes("workflow_steps")}:
        op.create_index(idx_last_progress, "workflow_steps", ["status", "last_progress_at"], unique=False)
    if idx_live_phase not in {i["name"] for i in sa.inspect(conn).get_indexes("workflow_steps")}:
        op.create_index(idx_live_phase, "workflow_steps", ["live_phase", "live_phase_started_at"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    existing = _workflow_steps_column_names(conn)
    idx_names = {i["name"] for i in sa.inspect(conn).get_indexes("workflow_steps")}

    if "ix_workflow_steps_live_phase_started_at" in idx_names:
        op.drop_index("ix_workflow_steps_live_phase_started_at", table_name="workflow_steps")
    if "ix_workflow_steps_status_last_progress_at" in idx_names:
        op.drop_index("ix_workflow_steps_status_last_progress_at", table_name="workflow_steps")

    for name in [
        "stuck_reason",
        "stuck_since",
        "live_attempt",
        "live_trace_id",
        "live_reason_detail",
        "live_reason_code",
        "live_phase_started_at",
        "live_phase",
        "last_activity_at",
        "last_progress_at",
    ]:
        if name in existing:
            op.drop_column("workflow_steps", name)
