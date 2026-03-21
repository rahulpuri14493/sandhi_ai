"""Tests for db/run_alembic_upgrade (SQLite path)."""

from db.run_alembic_upgrade import _existing_db_without_alembic_version, run_alembic_upgrade


def test_alembic_upgrade_skipped_for_sqlite():
    # In tests we use SQLite in-memory; upgrade should be a no-op and not raise.
    assert _existing_db_without_alembic_version() is False
    run_alembic_upgrade()

