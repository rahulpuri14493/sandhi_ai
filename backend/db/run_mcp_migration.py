"""
Run migrations on startup.

- 001–012 (pricing model, hiring, reviews, A2A, workflow, etc.): applied in order
  once per DB using schema_migrations table.
- 013–019 (MCP tables and tool config): applied if relevant tables do not exist.

Called after Base.metadata.create_all(). Skipped for SQLite (test DBs).
"""

import logging
import os
from sqlalchemy import text
from db.database import engine

# Migrations 001–012 are run automatically in order (once each, tracked in schema_migrations).
INITIAL_MIGRATION_PREFIX = (
    "001_",
    "002_",
    "003_",
    "004_",
    "005_",
    "006_",
    "007_",
    "008_",
    "009_",
    "010_",
    "011_",
    "012_",
)


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


def _ensure_schema_migrations_table(conn):
    """Create schema_migrations table if it does not exist (PostgreSQL)."""
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.commit()


def _migration_applied(conn, name: str) -> bool:
    """Return True if the migration name is already recorded."""
    r = conn.execute(
        text("SELECT 1 FROM schema_migrations WHERE name = :name"),
        {"name": name},
    )
    return r.scalar() is not None


def _record_migration(conn, name: str):
    """Record that a migration has been applied."""
    conn.execute(
        text("INSERT INTO schema_migrations (name) VALUES (:name)"),
        {"name": name},
    )
    conn.commit()


def _strip_leading_comments(stmt: str) -> str:
    """Remove leading comment lines so we don't skip statements that start with --."""
    lines = stmt.strip().splitlines()
    while lines and lines[0].strip().startswith("--"):
        lines.pop(0)
    return "\n".join(lines).strip()


def _split_sql_statements(sql: str):
    """Split SQL by ';' only when outside $$ ... $$ blocks (DO $$ ... END $$ stays one statement)."""
    statements = []
    start = 0
    i = 0
    in_dollar = False
    n = len(sql)
    while i < n:
        if not in_dollar and i + 1 < n and sql[i : i + 2] == "$$":
            in_dollar = True
            i += 2
            continue
        if in_dollar and i + 1 < n and sql[i : i + 2] == "$$":
            in_dollar = False
            i += 2
            continue
        if not in_dollar and sql[i] == ";":
            stmt = _strip_leading_comments(sql[start:i].strip())
            if stmt:
                statements.append(stmt)
            start = i + 1
            i += 1
            continue
        i += 1
    if start < n:
        stmt = _strip_leading_comments(sql[start:n].strip())
        if stmt:
            statements.append(stmt)
    return statements


def _run_sql_file(conn, path: str):
    """Execute a SQL file. Splits by statement while keeping DO $$ ... END $$ blocks together."""
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    statements = _split_sql_statements(sql)
    for stmt in statements:
        if not stmt.strip():
            continue
        try:
            conn.execute(text(stmt))
        except Exception:
            raise
    conn.commit()


def run_initial_migrations_if_needed():
    """Run migrations 001–012 in order if not already applied. Skipped for SQLite."""
    if engine.dialect.name == "sqlite":
        return
    migration_dir = _migration_dir()
    with engine.connect() as conn:
        _ensure_schema_migrations_table(conn)
        # List 001_*.sql through 012_*.sql in order
        for prefix in INITIAL_MIGRATION_PREFIX:
            candidates = [
                f
                for f in os.listdir(migration_dir)
                if f.startswith(prefix) and f.endswith(".sql")
            ]
            for filename in sorted(candidates):
                if _migration_applied(conn, filename):
                    continue
                path = os.path.join(migration_dir, filename)
                if not os.path.isfile(path):
                    continue
                try:
                    _run_sql_file(conn, path)
                    _record_migration(conn, filename)
                except Exception as e:
                    # Log but do not record so it can be retried
                    logging.getLogger(__name__).warning(
                        "Initial migration %s failed: %s", filename, e
                    )
                    raise


def run_mcp_migration_if_needed():
    """Run 013 if MCP tables do not exist; run 014–019 for tool types, endpoint_path, job/step tools, schema/business context, pageindex, tool_visibility.
    Skipped for SQLite: test DBs use Base.metadata.create_all() and migration SQL is PostgreSQL-specific.
    """
    if engine.dialect.name == "sqlite":
        return
    path_013 = os.path.join(_migration_dir(), "013_add_mcp_tables.sql")
    path_014 = os.path.join(_migration_dir(), "014_add_mcp_tool_types.sql")
    path_015 = os.path.join(_migration_dir(), "015_add_mcp_endpoint_path.sql")
    path_016 = os.path.join(_migration_dir(), "016_add_job_and_step_allowed_tools.sql")
    path_017 = os.path.join(
        _migration_dir(), "017_add_tool_schema_and_business_context.sql"
    )
    path_018 = os.path.join(_migration_dir(), "018_add_pageindex_tool_type.sql")
    path_019 = os.path.join(_migration_dir(), "019_add_tool_visibility_handoff.sql")
    with engine.connect() as conn:
        if os.path.isfile(path_013) and not _table_exists(
            conn, "mcp_server_connections"
        ):
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
        if os.path.isfile(path_018) and _table_exists(conn, "mcp_tool_configs"):
            with open(path_018, "r", encoding="utf-8") as f:
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
        if os.path.isfile(path_019) and _table_exists(conn, "jobs"):
            with open(path_019, "r", encoding="utf-8") as f:
                sql = f.read()
            for stmt in sql.strip().split(";"):
                s = stmt.strip()
                if s and not s.startswith("--"):
                    try:
                        conn.execute(text(s))
                    except Exception:
                        pass
            conn.commit()
