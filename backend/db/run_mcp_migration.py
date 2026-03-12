"""
Run MCP migration (013) if MCP tables do not exist.

Called after Base.metadata.create_all() so existing databases get
mcp_server_connections and mcp_tool_configs without manual psql.
Idempotent: uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS.
"""
import os
from sqlalchemy import text
from db.database import engine


def _migration_dir():
    return os.path.join(os.path.dirname(__file__), "..", "migrations")


def _table_exists(conn, table_name: str) -> bool:
    """Return True if the table exists. Works with PostgreSQL and SQLite (e.g. tests)."""
    dialect = conn.engine.dialect.name
    if dialect == "sqlite":
        r = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": table_name},
        )
    else:
        r = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": table_name},
        )
    return r.scalar() is not None


def run_mcp_migration_if_needed():
    """Run 013 if MCP tables do not exist; run 014–017 for tool types, endpoint_path, job/step tools, schema/business context.
    Skipped for SQLite: test DBs use Base.metadata.create_all() and migration SQL is PostgreSQL-specific."""
    if engine.dialect.name == "sqlite":
        return
    path_013 = os.path.join(_migration_dir(), "013_add_mcp_tables.sql")
    path_014 = os.path.join(_migration_dir(), "014_add_mcp_tool_types.sql")
    path_015 = os.path.join(_migration_dir(), "015_add_mcp_endpoint_path.sql")
    path_016 = os.path.join(_migration_dir(), "016_add_job_and_step_allowed_tools.sql")
    path_017 = os.path.join(_migration_dir(), "017_add_tool_schema_and_business_context.sql")
    with engine.connect() as conn:
        if os.path.isfile(path_013) and not _table_exists(conn, "mcp_server_connections"):
            with open(path_013, "r", encoding="utf-8") as f:
                conn.execute(text(f.read()))
            conn.commit()
        if os.path.isfile(path_014):
            with open(path_014, "r", encoding="utf-8") as f:
                sql = f.read()
            for line in sql.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                try:
                    conn.execute(text(line))
                except Exception:
                    pass
            conn.commit()
        if os.path.isfile(path_015) and _table_exists(conn, "mcp_server_connections"):
            with open(path_015, "r", encoding="utf-8") as f:
                conn.execute(text(f.read()))
            conn.commit()
        if os.path.isfile(path_016) and _table_exists(conn, "jobs"):
            with open(path_016, "r", encoding="utf-8") as f:
                sql = f.read()
            for line in sql.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                try:
                    conn.execute(text(line))
                except Exception:
                    pass
            conn.commit()
        if os.path.isfile(path_017) and _table_exists(conn, "mcp_tool_configs"):
            with open(path_017, "r", encoding="utf-8") as f:
                conn.execute(text(f.read()))
            conn.commit()
