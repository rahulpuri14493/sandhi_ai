"""Unit tests for services.db_schema_introspection (mocked DB drivers, no real servers)."""

import sys
import types
from collections import deque

import pytest

from services import db_schema_introspection as dsi


def test_introspect_postgres_missing_connection_string():
    schema, err = dsi.introspect_postgres({})
    assert schema is None and err == "Connection string is required"


def test_introspect_postgres_connect_fails(monkeypatch):
    fake_pg = types.SimpleNamespace(
        connect=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("refused"))
    )
    monkeypatch.setitem(sys.modules, "psycopg2", fake_pg)
    schema, err = dsi.introspect_postgres({"connection_string": "postgresql://x"})
    assert schema is None and "refused" in (err or "")


def test_introspect_postgres_success_one_table(monkeypatch):
    fetch_queue = deque(
        [
            [("orders",)],
            [
                ("id", "integer", "NO"),
                ("name", "text", "YES"),
            ],
            [("id",)],
            [("user_id", "users", "id")],
        ]
    )

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return fetch_queue.popleft()

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_pg = types.SimpleNamespace(
        connect=lambda *a, **k: FakeConn(),
    )
    monkeypatch.setitem(sys.modules, "psycopg2", fake_pg)
    schema, err = dsi.introspect_postgres({"connection_string": "postgresql://localhost/db"})
    assert err is None
    assert schema and len(schema["tables"]) == 1
    t = schema["tables"][0]
    assert t["name"] == "orders"
    assert len(t["columns"]) == 2
    assert t["primary_key"] == ["id"]
    assert t["foreign_keys"][0]["references_table"] == "users"


def test_introspect_postgres_inner_error_closes_connection(monkeypatch):
    fetch_queue = deque([[("t1",)]])

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            if not fetch_queue:
                raise RuntimeError("cursor broken")
            return fetch_queue.popleft()

        def close(self):
            pass

    closed = {"conn": False}

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            closed["conn"] = True

    fake_pg = types.SimpleNamespace(connect=lambda *a, **k: FakeConn())
    monkeypatch.setitem(sys.modules, "psycopg2", fake_pg)
    schema, err = dsi.introspect_postgres({"connection_string": "postgresql://x/y"})
    assert schema is None
    assert "broken" in (err or "").lower() or "cursor" in (err or "").lower()
    assert closed["conn"] is True


def test_introspect_mysql_import_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "pymysql", raising=False)
    monkeypatch.setitem(sys.modules, "pymysql", None)
    schema, err = dsi.introspect_mysql({"database": "d"})
    assert schema is None and "pymysql" in (err or "").lower()


def test_introspect_mysql_missing_database():
    schema, err = dsi.introspect_mysql({"host": "h"})
    assert schema is None and "database" in (err or "").lower()


def test_introspect_mysql_connect_fails(monkeypatch):
    fake_my = types.SimpleNamespace(
        connect=lambda **kw: (_ for _ in ()).throw(OSError("no route"))
    )
    monkeypatch.setitem(sys.modules, "pymysql", fake_my)
    schema, err = dsi.introspect_mysql(
        {"host": "x", "database": "db", "user": "u", "password": "p"}
    )
    assert schema is None and "route" in (err or "").lower()


def test_introspect_mysql_success(monkeypatch):
    fetch_queue = deque(
        [
            [("items",)],
            [("sku", "varchar", "NO")],
            [("sku",)],
            [],
        ]
    )

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return fetch_queue.popleft()

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_my = types.SimpleNamespace(connect=lambda **kw: FakeConn())
    monkeypatch.setitem(sys.modules, "pymysql", fake_my)
    schema, err = dsi.introspect_mysql({"database": "inventory", "host": "localhost"})
    assert err is None
    assert schema["tables"][0]["name"] == "items"
    assert schema["tables"][0]["primary_key"] == ["sku"]
    assert schema["tables"][0]["foreign_keys"] == []


def test_introspect_sqlserver_import_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "pymssql", raising=False)
    monkeypatch.setitem(sys.modules, "pymssql", None)
    schema, err = dsi.introspect_sqlserver(
        {"host": "h", "user": "u", "password": "p", "database": "d"}
    )
    assert schema is None and "pymssql" in (err or "").lower()


def test_introspect_sqlserver_missing_database():
    schema, err = dsi.introspect_sqlserver({"host": "h", "user": "u", "password": "p"})
    assert schema is None and "database" in (err or "").lower()


def test_introspect_sqlserver_encryption_kw_explicit(monkeypatch):
    seen = {}

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    def capture_connect(**kw):
        seen.update(kw)
        return FakeConn()

    fake_ms = types.SimpleNamespace(connect=capture_connect)
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    dsi.introspect_sqlserver(
        {
            "host": "sql.local",
            "user": "u",
            "password": "p",
            "database": "db",
            "encryption": "require",
        }
    )
    assert seen.get("encryption") == "require"


def test_introspect_sqlserver_azure_sets_encryption(monkeypatch):
    seen = {}

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    def capture_connect(**kw):
        seen.update(kw)
        return FakeConn()

    fake_ms = types.SimpleNamespace(connect=capture_connect)
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    dsi.introspect_sqlserver(
        {
            "host": "mydb.database.windows.net",
            "user": "u",
            "password": "p",
            "database": "db",
        }
    )
    assert seen.get("encryption") == "require"


def test_introspect_sqlserver_success_fk_with_schema(monkeypatch):
    fetch_queue = deque(
        [
            [("dbo", "orders")],
            [("id", "int", "NO")],
            [("id",)],
            [("user_id", "dbo", "users", "id")],
        ]
    )

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return fetch_queue.popleft()

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_ms = types.SimpleNamespace(connect=lambda **kw: FakeConn())
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    schema, err = dsi.introspect_sqlserver(
        {"host": "s", "user": "u", "password": "p", "database": "d"}
    )
    assert err is None
    t = schema["tables"][0]
    assert t["name"] == "dbo.orders"
    assert t["foreign_keys"][0]["references_table"] == "dbo.users"


def test_introspect_sqlserver_fk_no_schema_uses_table_only(monkeypatch):
    fetch_queue = deque(
        [
            [("dbo", "child")],
            [("x", "int", "YES")],
            [],
            [("pid", None, "parent", "id")],
        ]
    )

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return fetch_queue.popleft()

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_ms = types.SimpleNamespace(connect=lambda **kw: FakeConn())
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    schema, err = dsi.introspect_sqlserver(
        {"host": "s", "user": "u", "password": "p", "database": "d"}
    )
    assert err is None
    fk = schema["tables"][0]["foreign_keys"][0]
    assert fk["references_table"] == "parent"


def test_introspect_sql_tool_dispatches(monkeypatch):
    monkeypatch.setattr(dsi, "introspect_postgres", lambda c: ({"tables": []}, None))
    s, e = dsi.introspect_sql_tool("postgres", {})
    assert s == {"tables": []} and e is None

    monkeypatch.setattr(dsi, "introspect_mysql", lambda c: (None, "mysql err"))
    s, e = dsi.introspect_sql_tool("mysql", {})
    assert s is None and e == "mysql err"


def test_introspect_sql_tool_unsupported_message():
    schema, err = dsi.introspect_sql_tool("snowflake", {})
    assert schema is None and "snowflake" in (err or "").lower()


def test_format_schema_for_prompt_empty():
    assert dsi.format_schema_for_prompt({}) == ""
    assert dsi.format_schema_for_prompt({"tables": []}) == ""


def test_format_schema_for_prompt_nonempty_and_truncation():
    schema = {
        "tables": [
            {
                "name": "t1",
                "columns": [{"name": "a", "type": "int"}],
                "primary_key": ["a"],
                "foreign_keys": [
                    {
                        "columns": ["a"],
                        "references_table": "t2",
                        "references_columns": ["b"],
                    }
                ],
            }
        ]
    }
    out = dsi.format_schema_for_prompt(schema, max_chars=5000)
    assert "Table t1" in out and "Primary key: a" in out and "Foreign key:" in out
    short = dsi.format_schema_for_prompt(schema, max_chars=30)
    assert short.endswith("(schema truncated)")


def test_introspect_mysql_inner_query_error(monkeypatch):
    fetch_queue = deque([[("broken",)]])

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            if not fetch_queue:
                raise RuntimeError("sql gone")
            return fetch_queue.popleft()

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_my = types.SimpleNamespace(connect=lambda **kw: FakeConn())
    monkeypatch.setitem(sys.modules, "pymysql", fake_my)
    schema, err = dsi.introspect_mysql({"database": "db"})
    assert schema is None and "gone" in (err or "").lower()


def test_introspect_sqlserver_connect_fails(monkeypatch):
    fake_ms = types.SimpleNamespace(
        connect=lambda **kw: (_ for _ in ()).throw(ConnectionError("nope"))
    )
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    schema, err = dsi.introspect_sqlserver(
        {"host": "h", "user": "u", "password": "p", "database": "d"}
    )
    assert schema is None and "nope" in (err or "")


def test_introspect_sqlserver_inner_error(monkeypatch):
    fetch_queue = deque([[("dbo", "t")]])

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            if not fetch_queue:
                raise OSError("read fail")
            return fetch_queue.popleft()

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_ms = types.SimpleNamespace(connect=lambda **kw: FakeConn())
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    schema, err = dsi.introspect_sqlserver(
        {"host": "h", "user": "u", "password": "p", "database": "d"}
    )
    assert schema is None and "fail" in (err or "").lower()


def test_introspect_sql_tool_sqlserver_dispatch(monkeypatch):
    monkeypatch.setattr(dsi, "introspect_sqlserver", lambda c: ({"tables": [{"name": "x"}]}, None))
    s, e = dsi.introspect_sql_tool("sqlserver", {"database": "d"})
    assert e is None and s["tables"][0]["name"] == "x"
