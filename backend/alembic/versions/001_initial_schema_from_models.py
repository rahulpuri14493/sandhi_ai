"""Initial schema from SQLAlchemy models.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-15

Creates PostgreSQL enum types then all tables from Base.metadata.
For existing databases built with legacy SQL migrations (001-019), run once:
  alembic stamp head
to mark the DB as current without running this migration.
"""
from typing import Sequence, Union

from alembic import op

from db.database import Base

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Only pricingmodel is create_type=False in models; create it before create_all().
# Other enums are created by SQLAlchemy when create_all() runs.
_PG_ENUMS_CREATE_TYPE_FALSE = [
    ("pricingmodel", ["pay_per_use", "monthly", "quarterly"]),
]
# All enums for downgrade (drop in reverse order).
_PG_ENUMS_ALL = [
    "userrole", "agentstatus", "pricingmodel", "jobstatus", "qastatus",
    "transactionstatus", "earningsstatus", "hiringstatus", "nominationstatus",
    "mcptooltype",
]


def upgrade() -> None:
    """Create enum types that models skip (create_type=False), then all tables from Base.metadata."""
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        for name, values in _PG_ENUMS_CREATE_TYPE_FALSE:
            vals = ", ".join(repr(v) for v in values)
            op.execute(
                f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({vals}); "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
    Base.metadata.create_all(bind=conn)


def downgrade() -> None:
    """Drop all tables then enum types (PostgreSQL)."""
    conn = op.get_bind()
    Base.metadata.drop_all(bind=conn)
    if conn.dialect.name == "postgresql":
        for name in reversed(_PG_ENUMS_ALL):
            op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
