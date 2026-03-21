"""Legacy 002: fix pricingmodel enum if needed.

Revision ID: 004_legacy_002
Revises: 003_legacy_001
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004_legacy_002"
down_revision: Union[str, None] = "003_legacy_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    op.execute("""
        DO $$ BEGIN
            DROP TYPE IF EXISTS pricingmodel CASCADE;
            CREATE TYPE pricingmodel AS ENUM ('pay_per_use', 'monthly', 'quarterly');
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Error creating enum: %', SQLERRM;
        END $$;
    """)
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model pricingmodel DEFAULT 'pay_per_use'::pricingmodel")
    op.execute("UPDATE agents SET pricing_model = 'pay_per_use'::pricingmodel WHERE pricing_model IS NULL")


def downgrade() -> None:
    pass
