"""Branches for execution_sql engines and execution_common helpers (mocked / stubbed)."""
from __future__ import annotations

import json
import sys
import types
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import execution_common
from execution_sql import (
    execute_bigquery_sql,
    execute_databricks_sql,
    execute_elasticsearch,
    execute_mysql,
    execute_postgres,
    execute_snowflake_sql,
    execute_sqlserver_sql,
)

pytestmark = pytest.mark.unit


class TestPostgresReadEdges:
    def test_read_no_rows(self, monkeypatch):
        import psycopg2

        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.description = [("c",)]
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: conn)
        out = execute_postgres(
            {"connection_string": "postgresql://u:p@localhost/db"},
            {"query": "SELECT 1 WHERE 1=0"},
        )
        assert "No rows returned" in out

    def test_read_error_safe_message(self, monkeypatch):
        import psycopg2

        def _boom(*_a, **_k):
            raise RuntimeError("x")

        monkeypatch.setattr(psycopg2, "connect", _boom)
        out = execute_postgres(
            {"connection_string": "postgresql://u:p@localhost/db"},
            {"query": "SELECT 1"},
        )
        assert "Postgres read error" in out


class TestBigQuerySnowflakeEdges:
    def test_bigquery_no_rows(self, monkeypatch):
        job = MagicMock()
        job.result.return_value = []
        client = MagicMock()
        client.query.return_value = job
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        bq_mod = types.ModuleType("google.cloud.bigquery")
        bq_mod.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq_mod)
        sa_mod = types.ModuleType("google.oauth2.service_account")

        class _C:
            @staticmethod
            def from_service_account_info(_i):
                return MagicMock()

        sa_mod.Credentials = _C
        monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)
        out = execute_bigquery_sql({"project_id": "p"}, {"query": "SELECT 1 WHERE 1=0"})
        assert "No rows returned" in out

    def test_snowflake_read_no_rows(self, monkeypatch):
        class _Cur:
            description = (("a",),)

            def execute(self, _q):
                return None

            def fetchall(self):
                return []

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        _stub_snowflake_connector(monkeypatch, _Conn())
        out = execute_snowflake_sql(
            {"account": "a", "user": "u", "password": "p"},
            {"query": "SELECT 1 WHERE 0=1"},
        )
        assert "No rows returned" in out


class TestPostgresMysqlWritePaths:
    def test_postgres_write_with_params_mocked(self, monkeypatch):
        import psycopg2

        cur = MagicMock()
        cur.rowcount = 3
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: conn)
        out = execute_postgres(
            {"connection_string": "postgresql://u:p@localhost/db", "query": "UPDATE t SET a=1 WHERE id=%s"},
            {"params": (5,)},
        )
        assert json.loads(out)["rowcount"] == 3
        cur.execute.assert_called_once()

    def test_mysql_write_mocked(self, monkeypatch):
        import pymysql

        cur = MagicMock()
        cur.rowcount = 1
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymysql, "connect", lambda **kw: conn)
        out = execute_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d", "query": "UPDATE t SET x=1"},
            {},
        )
        assert json.loads(out)["status"] == "ok"

    def test_mysql_secure_transport_message(self, monkeypatch):
        import pymysql

        def boom(**kw):
            raise pymysql.err.OperationalError(1109, "require_secure_transport=ON")

        monkeypatch.setattr(pymysql, "connect", boom)
        out = execute_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"query": "SELECT 1"},
        )
        assert "secure transport" in out.lower() or "tls" in out.lower()

    def test_mysql_read_with_params(self, monkeypatch):
        import pymysql

        cur = MagicMock()
        cur.fetchall.return_value = [(2,)]
        cur.description = [("n",)]
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymysql, "connect", lambda **kw: conn)
        out = execute_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"query": "SELECT %s AS n", "params": (2,)},
        )
        assert "2" in out and "n" in out.splitlines()[0]

    def test_mysql_programming_error_message(self, monkeypatch):
        import pymysql

        def _boom(**_kw):
            raise pymysql.err.ProgrammingError(1064, "bad sql")

        monkeypatch.setattr(pymysql, "connect", _boom)
        out = execute_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"query": "SELECT 1"},
        )
        assert "Error" in out


class TestPostgresWriteError:
    def test_write_connect_failure(self, monkeypatch):
        import psycopg2

        def _boom(*_a, **_k):
            raise RuntimeError("down")

        monkeypatch.setattr(psycopg2, "connect", _boom)
        out = execute_postgres({"connection_string": "postgresql://x/y", "query": "UPDATE t SET a=1"}, {})
        assert "Postgres write error" in out


class TestElasticsearch:
    def test_missing_query(self):
        assert "query is required" in execute_elasticsearch({"url": "http://localhost:9200"}, {})

    def test_success_200(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"hits": []}
        mock_http = MagicMock()
        mock_http.post.return_value = mock_resp
        with patch("execution_http.get_sync_http_client", return_value=mock_http):
            out = execute_elasticsearch(
                {"url": "http://localhost:9200"},
                {"query": "text", "index": "idx", "size": 5},
            )
        assert "hits" in out

    def test_non_200(self):
        mock_resp = MagicMock(status_code=503)
        mock_resp.text = "unavailable"
        mock_http = MagicMock()
        mock_http.post.return_value = mock_resp
        with patch("execution_http.get_sync_http_client", return_value=mock_http):
            out = execute_elasticsearch({"url": "http://h:9200"}, {"query": "q"})
        assert "503" in out

    def test_network_error_safe_message(self):
        mock_http = MagicMock()
        mock_http.post.side_effect = RuntimeError("down")
        with patch("execution_http.get_sync_http_client", return_value=mock_http):
            out = execute_elasticsearch({"url": "http://h:9200"}, {"query": "q"})
        assert "Elasticsearch error" in out


def _stub_snowflake_connector(monkeypatch, mock_conn: Any = None) -> None:
    # `import snowflake.connector` binds local name `snowflake` (the package); code uses snowflake.connector.connect.
    pkg = types.ModuleType("snowflake")
    fake_sf = types.ModuleType("snowflake.connector")
    fake_sf.connect = MagicMock(return_value=mock_conn if mock_conn is not None else MagicMock())
    pkg.connector = fake_sf
    monkeypatch.setitem(sys.modules, "snowflake", pkg)
    monkeypatch.setitem(sys.modules, "snowflake.connector", fake_sf)


class TestSnowflakeBigQueryDatabricksErrors:
    def test_snowflake_write_execute_raises(self, monkeypatch):
        class _Cur:
            def execute(self, _q):
                raise RuntimeError("fail")

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        _stub_snowflake_connector(monkeypatch, _Conn())
        out = execute_snowflake_sql(
            {"account": "a", "user": "u", "password": "p", "query": "DELETE FROM t"},
            {},
        )
        assert "Snowflake SQL error" in out

    def test_bigquery_job_result_raises(self, monkeypatch):
        job = MagicMock()
        job.result.side_effect = RuntimeError("bq")
        client = MagicMock()
        client.query.return_value = job
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        bq_mod = types.ModuleType("google.cloud.bigquery")
        bq_mod.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq_mod)
        sa_mod = types.ModuleType("google.oauth2.service_account")

        class _C:
            @staticmethod
            def from_service_account_info(_i):
                return MagicMock()

        sa_mod.Credentials = _C
        monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)
        out = execute_bigquery_sql({"project_id": "p"}, {"query": "SELECT 1"})
        assert "BigQuery error" in out

    def test_databricks_read_raises(self, monkeypatch):
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = RuntimeError("dbx")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        _stub_databricks_sql(monkeypatch)
        sys.modules["databricks.sql"].connect = MagicMock(return_value=mock_conn)
        out = execute_databricks_sql(
            {
                "host": "https://adb.com",
                "token": "t",
                "http_path": "/sql/1.0/warehouses/wh",
            },
            {"query": "SELECT 1"},
        )
        assert "Databricks error" in out


class TestSnowflakeWritePath:
    def test_write_returns_ok_json(self, monkeypatch):
        class _Cur:
            def execute(self, _q):
                return None

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        fake_sf = types.ModuleType("snowflake.connector")
        pkg = types.ModuleType("snowflake")
        pkg.connector = fake_sf
        fake_sf.connect = MagicMock(return_value=_Conn())
        monkeypatch.setitem(sys.modules, "snowflake", pkg)
        monkeypatch.setitem(sys.modules, "snowflake.connector", fake_sf)
        out = execute_snowflake_sql(
            {
                "account": "a",
                "user": "u",
                "password": "p",
                "query": "DELETE FROM staging WHERE job_id = 1",
            },
            {},
        )
        assert json.loads(out)["status"] == "ok"


class TestSnowflakeSql:
    def test_missing_query(self, monkeypatch):
        _stub_snowflake_connector(monkeypatch)
        out = execute_snowflake_sql({"account": "a", "user": "u", "password": "p"}, {})
        assert "query" in out.lower()

    def test_read_path_mocked(self, monkeypatch):
        class _Cur:
            description = (("c1",), ("c2",))

            def execute(self, _q):
                return None

            def fetchall(self):
                return [(1, "x")]

            def close(self):
                return None

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                return None

        _stub_snowflake_connector(monkeypatch, _Conn())
        out = execute_snowflake_sql(
            {"account": "a", "user": "u", "password": "p", "database": "db", "schema": "s"},
            {"query": "SELECT 1 AS c1, 2 AS c2"},
        )
        assert "c1" in out and "x" in out


class TestBigQuerySql:
    def _install_bq(self, monkeypatch, client: MagicMock) -> None:
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        bq_mod = types.ModuleType("google.cloud.bigquery")
        bq_mod.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq_mod)
        sa_mod = types.ModuleType("google.oauth2.service_account")

        class _C:
            @staticmethod
            def from_service_account_info(_i):
                return MagicMock()

        sa_mod.Credentials = _C
        monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)

    def test_missing_query(self, monkeypatch):
        c = MagicMock()
        self._install_bq(monkeypatch, c)
        out = execute_bigquery_sql({"project_id": "p"}, {})
        assert "query" in out.lower()

    def test_select_returns_tsv(self, monkeypatch):
        job = MagicMock()
        job.result.return_value = [{"a": 1, "b": "z"}]
        client = MagicMock()
        client.query.return_value = job
        self._install_bq(monkeypatch, client)
        out = execute_bigquery_sql({"project_id": "p"}, {"query": "SELECT 1"})
        assert "a" in out.splitlines()[0]
        assert "1" in out

    def test_write_returns_job_id(self, monkeypatch):
        job = MagicMock()
        job.job_id = "job-xyz"
        client = MagicMock()
        client.query.return_value = job
        self._install_bq(monkeypatch, client)
        out = execute_bigquery_sql(
            {"project_id": "p", "query": "CREATE TABLE x (a INT)"},
            {},
        )
        assert "job-xyz" in out


class TestSqlServerSql:
    def test_readonly_blocks_write(self, monkeypatch):
        monkeypatch.setenv("MCP_SQLSERVER_INTERACTIVE_READONLY", "true")
        out = execute_sqlserver_sql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"query": "DELETE FROM t"},
        )
        assert "read-only" in out.lower()

    def test_read_select_mocked(self, monkeypatch):
        import pymssql

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [(3,)]
        mock_cur.description = [("n",)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        monkeypatch.setattr(pymssql, "connect", lambda **kw: mock_conn)
        out = execute_sqlserver_sql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"query": "SELECT 3 AS n"},
        )
        assert "n" in out and "3" in out

    def test_write_mocked(self, monkeypatch):
        import pymssql

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        monkeypatch.setattr(pymssql, "connect", lambda **kw: mock_conn)
        out = execute_sqlserver_sql(
            {
                "host": "h",
                "user": "u",
                "password": "p",
                "database": "d",
                "query": "UPDATE t SET x=1",
            },
            {},
        )
        assert json.loads(out)["status"] == "ok"

    def test_connect_failure_returns_error(self, monkeypatch):
        import pymssql

        def _boom(**_kw):
            raise RuntimeError("mssql down")

        monkeypatch.setattr(pymssql, "connect", _boom)
        out = execute_sqlserver_sql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"query": "SELECT 1"},
        )
        assert "SQL Server error" in out or "Error" in out


def _stub_databricks_sql(monkeypatch) -> None:
    dsql = types.ModuleType("databricks.sql")
    dsql.connect = MagicMock()
    pkg = types.ModuleType("databricks")
    pkg.sql = dsql
    monkeypatch.setitem(sys.modules, "databricks", pkg)
    monkeypatch.setitem(sys.modules, "databricks.sql", dsql)


class TestDatabricksWritePath:
    def test_write_non_read_returns_ok(self, monkeypatch):
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        _stub_databricks_sql(monkeypatch)
        sys.modules["databricks.sql"].connect = MagicMock(return_value=mock_conn)
        out = execute_databricks_sql(
            {
                "host": "https://adb.com",
                "token": "t",
                "http_path": "/sql/1.0/warehouses/wh",
                "query": "DELETE FROM t WHERE 1=0",
            },
            {},
        )
        assert json.loads(out)["status"] == "ok"


class TestDatabricksSql:
    def test_missing_host_token(self, monkeypatch):
        _stub_databricks_sql(monkeypatch)
        out = execute_databricks_sql(
            {"http_path": "/sql/1.0/warehouses/wh"},
            {"query": "SELECT 1"},
        )
        assert "host" in out.lower() and "token" in out.lower()

    def test_missing_http_path(self, monkeypatch):
        _stub_databricks_sql(monkeypatch)
        out = execute_databricks_sql(
            {"host": "https://adb.com", "token": "t"},
            {"query": "SELECT 1"},
        )
        assert "http_path" in out.lower() or "warehouse" in out.lower()

    def test_select_mocked(self, monkeypatch):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [(1,)]
        mock_cur.description = [("x",)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        _stub_databricks_sql(monkeypatch)
        sys.modules["databricks.sql"].connect = MagicMock(return_value=mock_conn)

        out = execute_databricks_sql(
            {
                "host": "https://adb-123.azuredatabricks.net",
                "token": "dapi",
                "sql_warehouse_id": "w1",
            },
            {"query": "SELECT 1 AS x"},
        )
        assert "x" in out


class TestMergeSqlDialect:
    def test_snowflake_sqlserver_bigquery_merge_templates(self):
        sql_sf = execution_common._merge_sql_dialect(
            "snowflake", "t", ["a", "b"], ["a"], "#stg"
        )
        assert "MERGE INTO t" in sql_sf and "WHEN NOT MATCHED" in sql_sf

        sql_ss = execution_common._merge_sql_dialect(
            "sqlserver", "[d].[t]", ["x", "y"], ["x"], "#tmp_mcp_" + "a" * 32
        )
        assert "MERGE INTO [d].[t]" in sql_ss and "[x]" in sql_ss

        sql_bq = execution_common._merge_sql_dialect(
            "bigquery", "`p.d.t`", ["c1", "c2"], ["c1"], "stg"
        )
        assert "MERGE `p.d.t`" in sql_bq

    def test_merge_dialect_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported merge dialect"):
            execution_common._merge_sql_dialect("mysql", "t", ["a"], ["a"], "s")

    def test_snowflake_merge_only_merge_columns_uses_key_update(self):
        sql = execution_common._merge_sql_dialect("snowflake", "t", ["id"], ["id"], "#stg")
        assert "tgt.id" in sql and "src.id" in sql


class TestSqlserverStagingHelpers:
    def test_staging_insert_and_drop_and_merge_execute(self):
        temp = "#tmp_mcp_" + "a" * 32
        ins = execution_common._sqlserver_staging_insert_sql(temp, "[x]", "%s")
        assert "INSERT INTO" in ins
        cur = MagicMock()
        fq = "[dbo].[T]"
        execution_common._pymssql_sqlserver_select_into_empty_clone(cur, temp, fq)
        merge = execution_common._merge_sql_dialect("sqlserver", fq, ["id", "n"], ["id"], temp)
        execution_common._pymssql_sqlserver_execute_merge_artifact(cur, merge)
        execution_common._pymssql_sqlserver_drop_staging(cur, temp)
        assert cur.execute.call_count >= 3

    def test_staging_bad_name_raises(self):
        with pytest.raises(ValueError):
            execution_common._sqlserver_staging_insert_sql("#bad", "a", "%s")


class TestSqlserverMergeValidate:
    def test_validate_accepts_generated_merge(self):
        temp = execution_common._sqlserver_staging_temp_name()
        merge = execution_common._merge_sql_dialect(
            "sqlserver", "[dbo].[Jobs]", ["id", "name"], ["id"], temp
        )
        execution_common._sqlserver_validate_merge_sql(merge)

    def test_validate_rejects_comments(self):
        with pytest.raises(ValueError, match="comment"):
            execution_common._sqlserver_validate_merge_sql("MERGE INTO --x")


class TestReadArtifactAndS3Helpers:
    def test_read_artifact_local_file(self, tmp_path, monkeypatch):
        root = tmp_path / "uploads"
        root.mkdir()
        f = root / "job1" / "out.jsonl"
        f.parent.mkdir(parents=True)
        f.write_bytes(b'{"x":1}\n')
        monkeypatch.setattr(execution_common, "_ARTIFACT_ROOT", str(root.resolve()))
        data = execution_common.read_artifact_bytes({"path": "uploads/jobs/job1/out.jsonl"})
        assert b'"x"' in data

    def test_s3_client_uses_env_credentials(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ak")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sk")
        mock_client = MagicMock()
        with patch("boto3.client", return_value=mock_client) as m_cli:
            c = execution_common._s3_client_for_config()
        assert c is mock_client
        m_cli.assert_called_once()
        call_kw = m_cli.call_args.kwargs
        assert call_kw.get("aws_access_key_id") == "ak"

    def test_read_artifact_s3_mocked(self, monkeypatch):
        mock_body = MagicMock()
        mock_body.read.return_value = b"s3data"
        mock_cli = MagicMock()
        mock_cli.get_object.return_value = {"Body": mock_body}
        monkeypatch.setattr(execution_common, "_s3_client_for_config", lambda **kw: mock_cli)
        data = execution_common.read_artifact_bytes(
            {"storage": "s3", "bucket": "mybucket", "key": "uploads/k.jsonl", "path": ""},
        )
        assert data == b"s3data"

    def test_read_artifact_invalid_s3_key_raises(self):
        with pytest.raises(ValueError, match="unsafe"):
            execution_common.read_artifact_bytes(
                {"storage": "s3", "bucket": "b", "key": "../evil", "path": ""},
            )

    def test_s3_bucket_key_guard(self):
        assert execution_common._s3_artifact_bucket_key_ok("", "k") is False
        assert execution_common._s3_artifact_bucket_key_ok("b", "ok/path") is True
        assert execution_common._s3_artifact_bucket_key_ok("b", "a/../b") is False

    def test_parse_artifact_csv_and_bad_format(self):
        rows = execution_common.parse_artifact_records(b"a,b\n1,2\n", "csv")
        assert rows[0]["a"] == "1"
        with pytest.raises(ValueError, match="Unsupported"):
            execution_common.parse_artifact_records(b"x", "parquet")

    def test_parse_json_list_and_scalar_returns_empty(self):
        rows = execution_common.parse_artifact_records(b'[{"a":1},{"a":2}]', "json")
        assert len(rows) == 2
        assert execution_common.parse_artifact_records(b'"scalar"', "json") == []

    def test_resolve_local_bad_path(self):
        assert execution_common.resolve_local_artifact_path("uploads/jobs/../x") is None


class TestExecutionCommonHelpers:
    def test_url_for_log_strips_credentials(self):
        u = execution_common._url_for_log("https://user:pass@db.example.com:5432/mydb")
        assert "user" not in u
        assert "db.example.com" in u

    def test_url_for_log_empty(self):
        assert execution_common._url_for_log("") == ""
        assert execution_common._url_for_log("http:///nohost/path") == "<invalid-url>"

    def test_postgres_dest_hint(self):
        assert "myhost" in execution_common._postgres_dest_hint("postgresql://u:p@myhost:5432/dbname")
        assert execution_common._postgres_dest_hint("bad") == "postgresql"

    def test_redact_object_store_key(self):
        r = execution_common._redact_object_store_key_for_log("secret/path/key")
        assert "secret" not in r
        assert "len=" in r

    def test_resolve_s3_minio_console_port(self, monkeypatch):
        monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
        ep = execution_common._resolve_s3_compatible_endpoint(
            "minio",
            {"endpoint": "http://localhost:9001"},
        )
        assert ":9000" in (ep or "")

    def test_resolve_s3_loopback_replaced_by_env(self, monkeypatch):
        monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
        ep = execution_common._resolve_s3_compatible_endpoint(
            "minio",
            {"endpoint": "http://127.0.0.1:9000"},
        )
        assert "minio" in (ep or "")
