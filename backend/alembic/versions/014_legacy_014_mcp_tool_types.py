"""Legacy 014: add MCP tool types to mcptooltype enum.

Revision ID: 014_legacy_014
Revises: 013_legacy_013
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "014_legacy_014"
down_revision: Union[str, None] = "013_legacy_013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALUES = [
    "pinecone", "weaviate", "qdrant", "chroma", "mysql", "elasticsearch",
    "s3", "slack", "github", "notion", "rest_api",
]


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    for v in _VALUES:
        op.execute(f"DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE '{v}'; EXCEPTION WHEN duplicate_object THEN null; END $$;")


def downgrade() -> None:
    pass  # PostgreSQL cannot remove enum values easily
