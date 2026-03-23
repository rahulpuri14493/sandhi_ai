"""Postgres interactive SQL guards (read-only mode)."""
import pytest

from execution_sql import execute_postgres

pytestmark = pytest.mark.unit


def test_interactive_readonly_blocks_insert(monkeypatch):
    monkeypatch.setenv("MCP_POSTGRES_INTERACTIVE_READONLY", "true")
    out = execute_postgres(
        {
            "connection_string": "postgresql://u:p@localhost:5432/db",
            "query": "INSERT INTO daily_job_creation VALUES (1)",
        },
        {},
    )
    assert "read-only" in out.lower()
    assert "output_contract" in out.lower()


def test_interactive_readonly_allows_select(monkeypatch):
    monkeypatch.setenv("MCP_POSTGRES_INTERACTIVE_READONLY", "true")
    # No real DB: connect will fail if we hit read path — use invalid conn to short-circuit after readonly check
    # Actually readonly allows select then tries connect — need mock
    import psycopg2
    from unittest.mock import MagicMock

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_cur.description = None
    mock_conn.cursor.return_value = mock_cur
    monkeypatch.setattr(psycopg2, "connect", lambda conn_str: mock_conn)

    out = execute_postgres(
        {
            "connection_string": "postgresql://u:p@localhost:5432/db",
            "query": "SELECT 1 AS x",
        },
        {},
    )
    assert "Error" not in out or "read-only" not in out.lower()
    assert mock_cur.execute.called
