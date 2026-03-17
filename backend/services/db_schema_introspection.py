"""
Introspect Postgres/MySQL database schema for MCP SQL tools.
Returns tables, columns (name, type, nullable), primary keys, and foreign keys.
Used to populate schema_metadata so the agent has database context when writing SQL.
"""

import json
from typing import Any, Dict, List, Optional, Tuple


def introspect_postgres(config: dict) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Introspect PostgreSQL via information_schema. Returns (schema_dict, error_message).
    config must have "connection_string".
    """
    conn_str = (config.get("connection_string") or "").strip()
    if not conn_str:
        return None, "Connection string is required"
    try:
        import psycopg2

        conn = psycopg2.connect(conn_str, connect_timeout=10)
        cur = conn.cursor()
    except Exception as e:
        return None, str(e)

    try:
        # Tables in public schema (and optionally others)
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        table_names = [row[0] for row in cur.fetchall()]

        tables: List[Dict[str, Any]] = []
        for tname in table_names:
            # Columns
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """,
                (tname,),
            )
            columns = [
                {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
                for row in cur.fetchall()
            ]

            # Primary key
            cur.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) AND a.attisdropped = false
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = %s AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
            """,
                (tname,),
            )
            pk = [row[0] for row in cur.fetchall()]

            # Foreign keys
            cur.execute(
                """
                SELECT kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
                WHERE tc.table_schema = 'public' AND tc.table_name = %s AND tc.constraint_type = 'FOREIGN KEY'
                ORDER BY kcu.ordinal_position
            """,
                (tname,),
            )
            fk_rows = cur.fetchall()
            foreign_keys = [
                {
                    "columns": [r[0]],
                    "references_table": r[1],
                    "references_columns": [r[2]],
                }
                for r in fk_rows
            ]

            tables.append(
                {
                    "name": tname,
                    "columns": columns,
                    "primary_key": pk,
                    "foreign_keys": foreign_keys,
                }
            )

        cur.close()
        conn.close()
        return {"tables": tables}, None
    except Exception as e:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
        return None, str(e)


def introspect_mysql(config: dict) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Introspect MySQL via information_schema. Returns (schema_dict, error_message).
    config must have host, database; optional port, user, password.
    """
    try:
        import pymysql
    except ImportError:
        return None, "MySQL introspection requires pymysql (not installed in backend)"

    host = config.get("host", "localhost")
    port = int(config.get("port", 3306))
    user = config.get("user", "")
    password = config.get("password", "")
    database = (config.get("database") or "").strip()
    if not database:
        return None, "Database name is required"

    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=10,
        )
        cur = conn.cursor()
    except Exception as e:
        return None, str(e)

    try:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """,
            (database,),
        )
        table_names = [row[0] for row in cur.fetchall()]

        tables: List[Dict[str, Any]] = []
        for tname in table_names:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
            """,
                (database, tname),
            )
            columns = [
                {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT column_name FROM information_schema.statistics
                WHERE table_schema = %s AND table_name = %s AND index_name = 'PRIMARY'
                ORDER BY seq_in_index
            """,
                (database, tname),
            )
            pk = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                SELECT column_name, referenced_table_name, referenced_column_name
                FROM information_schema.key_column_usage
                WHERE table_schema = %s AND table_name = %s
                  AND referenced_table_schema IS NOT NULL
                ORDER BY ordinal_position
            """,
                (database, tname),
            )
            fk_rows = cur.fetchall()
            foreign_keys = [
                {
                    "columns": [r[0]],
                    "references_table": r[1],
                    "references_columns": [r[2]],
                }
                for r in fk_rows
            ]

            tables.append(
                {
                    "name": tname,
                    "columns": columns,
                    "primary_key": pk,
                    "foreign_keys": foreign_keys,
                }
            )

        cur.close()
        conn.close()
        return {"tables": tables}, None
    except Exception as e:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
        return None, str(e)


def introspect_sql_tool(
    tool_type: str, config: dict
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Introspect a SQL tool by type. Returns (schema_dict, error_message).
    schema_dict is suitable for JSON serialization and storage in schema_metadata.
    """
    if tool_type == "postgres":
        return introspect_postgres(config)
    if tool_type == "mysql":
        return introspect_mysql(config)
    return None, f"Schema introspection not supported for tool type: {tool_type}"


def format_schema_for_prompt(schema_dict: Dict[str, Any], max_chars: int = 8000) -> str:
    """
    Format introspected schema as compact text for the agent's system message.
    Truncates if over max_chars.
    """
    if not schema_dict or not schema_dict.get("tables"):
        return ""
    lines: List[str] = []
    for t in schema_dict["tables"]:
        name = t.get("name", "?")
        cols = t.get("columns", [])
        col_str = ", ".join(f"{c.get('name', '')} ({c.get('type', '')})" for c in cols)
        lines.append(f"  Table {name}: {col_str}")
        pk = t.get("primary_key") or []
        if pk:
            lines.append(f"    Primary key: {', '.join(pk)}")
        fks = t.get("foreign_keys") or []
        for fk in fks:
            ref = fk.get("references_table", "?")
            ref_cols = fk.get("references_columns", [])
            fk_cols = fk.get("columns", [])
            lines.append(
                f"    Foreign key: {', '.join(fk_cols)} -> {ref}({', '.join(ref_cols)})"
            )
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n(schema truncated)"
    return out
