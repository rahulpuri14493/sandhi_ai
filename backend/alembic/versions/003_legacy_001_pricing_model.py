"""Legacy 001: add pricing model and subscription price columns to agents.

Revision ID: 003_legacy_001
Revises: 002_indexes
Create Date: 2026-03-15

Idempotent: safe to run after 001_initial (full schema).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_legacy_001"
down_revision: Union[str, None] = "002_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_first_enum_label(conn, enum_name: str) -> str:
    """Return the first label of a PostgreSQL enum type (used to detect case convention)."""
    result = conn.execute(
        sa.text(
            "SELECT enumlabel FROM pg_enum "
            "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
            "WHERE pg_type.typname = :name ORDER BY enumsortorder LIMIT 1"
        ),
        {"name": enum_name},
    )
    row = result.first()
    return row[0] if row else ""


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    # Detect whether enum uses uppercase labels (legacy SQL) or lowercase (Alembic-created).
    first_label = _get_first_enum_label(conn, "pricingmodel")
    pay_per_use = "PAY_PER_USE" if first_label.isupper() else "pay_per_use"

    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel")
    op.execute(f"ALTER TABLE agents ALTER COLUMN pricing_model SET DEFAULT '{pay_per_use}'::pricingmodel")
    op.execute(f"UPDATE agents SET pricing_model = '{pay_per_use}'::pricingmodel WHERE pricing_model IS NULL")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS monthly_price FLOAT")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS quarterly_price FLOAT")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS pricing_model")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS monthly_price")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS quarterly_price")
