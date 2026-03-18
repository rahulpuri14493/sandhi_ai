"""
Run Alembic migrations on application startup (PostgreSQL only).

Zero manual intervention: works for both new databases (runs all migrations)
and existing databases (legacy SQL or pre-Alembic). Detects existing schema
and stamps the initial revision so only pending migrations run.
Skipped for SQLite (tests). Safe for multi-process: alembic_version serializes.
"""
import logging
import os
import sys

from sqlalchemy import text

from db.database import engine

log = logging.getLogger(__name__)

# First revision ID; used to stamp existing DBs so we don't re-run initial migration.
INITIAL_REVISION = "001_initial"


def _existing_db_without_alembic_version() -> bool:
    """True if DB has application tables but no alembic_version row (legacy or pre-Alembic)."""
    if engine.dialect.name != "postgresql":
        return False
    try:
        with engine.connect() as conn:
            # 1) Alembic version table missing -> check for app tables (existing DB from legacy SQL)
            has_alembic_table = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
                )
            ).scalar() is not None

            if not has_alembic_table:
                has_app_tables = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name IN ('users', 'agents', 'jobs') LIMIT 1"
                    )
                ).scalar() is not None
                return has_app_tables

            # 2) alembic_version exists: if it has a row, we're already versioned
            if conn.execute(text("SELECT 1 FROM alembic_version LIMIT 1")).scalar() is not None:
                return False

            # 3) alembic_version exists but empty; treat as existing if app tables present
            return conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name IN ('users', 'agents', 'jobs') LIMIT 1"
                )
            ).scalar() is not None
    except Exception:
        return False


def run_alembic_upgrade() -> None:
    """Run Alembic upgrade head. Auto-stamps existing DBs at initial revision so no manual step."""
    if engine.dialect.name != "postgresql":
        return
    from alembic import command
    from alembic.config import Config

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_file = os.path.join(backend_dir, "alembic.ini")
    if not os.path.isfile(config_file):
        log.warning("alembic.ini not found at %s; skipping Alembic upgrade", config_file)
        return
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.chdir(backend_dir)
    alembic_cfg = Config(config_file)
    database_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRESQLCONNSTR_DefaultConnection")
        or os.getenv("CUSTOMCONNSTR_DefaultConnection")
    )
    if database_url:
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    try:
        if _existing_db_without_alembic_version():
            log.info("Existing database detected without Alembic version; stamping %s", INITIAL_REVISION)
            command.stamp(alembic_cfg, INITIAL_REVISION)
        command.upgrade(alembic_cfg, "head")
        log.info("Alembic upgrade head completed.")
    except Exception as e:
        log.exception("Alembic upgrade failed: %s", e)
        raise
