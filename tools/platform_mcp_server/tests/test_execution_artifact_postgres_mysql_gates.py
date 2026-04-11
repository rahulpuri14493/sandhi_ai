"""Direct tests for _artifact_write_postgres / _artifact_write_mysql gates and happy paths (mocked DB)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import execution_artifact as ea

pytestmark = pytest.mark.unit

_REC = [{"id": 1, "name": "x"}]
_CFG_PG = {"connection_string": "postgresql://u:p@localhost:5432/db"}
_TGT_PG = {"schema": "public", "table": "items"}


class TestArtifactPostgresGates:
    def test_missing_connection_string(self):
        out = ea._artifact_write_postgres({}, _TGT_PG, _REC, [], "append", "append")
        assert "connection_string" in out.lower()

    def test_missing_table(self):
        out = ea._artifact_write_postgres(_CFG_PG, {"schema": "public", "table": ""}, _REC, [], "append", "append")
        assert "target.table" in out.lower()

    def test_invalid_schema_name(self):
        out = ea._artifact_write_postgres(_CFG_PG, {"schema": "1bad", "table": "t"}, _REC, [], "append", "append")
        assert "invalid schema" in out.lower()

    def test_merge_key_not_in_columns(self):
        out = ea._artifact_write_postgres(
            _CFG_PG, _TGT_PG, [{"a": 1}], ["missing"], "upsert", "upsert"
        )
        assert "merge_keys" in out.lower()

    def test_invalid_column_key(self):
        out = ea._artifact_write_postgres(
            _CFG_PG, _TGT_PG, [{"ok": 1, "bad col": 2}], [], "append", "append"
        )
        assert "invalid column name" in out.lower()

    def test_append_executemany_ok(self, monkeypatch):
        class _Cur:
            rowcount = 2

            def executemany(self, *_a, **_k):
                return None

            def execute(self, *_a, **_k):
                return None

            def close(self):
                return None

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                return None

            def close(self):
                return None

        import psycopg2

        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: _Conn())
        out = ea._artifact_write_postgres(_CFG_PG, _TGT_PG, _REC, [], "append", "append")
        assert json.loads(out)["status"] == "ok"


class TestArtifactMysqlGates:
    def test_missing_database(self):
        out = ea._artifact_write_mysql(
            {"host": "h", "user": "u", "password": "p"},
            {"table": "t"},
            _REC,
            [],
            "append",
            "append",
        )
        assert "database" in out.lower()

    def test_append_ok_mocked(self, monkeypatch):
        import pymysql

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymysql, "connect", lambda **kw: conn)
        out = ea._artifact_write_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"database": "d", "table": "items"},
            _REC,
            [],
            "append",
            "append",
        )
        assert json.loads(out)["status"] == "ok"
