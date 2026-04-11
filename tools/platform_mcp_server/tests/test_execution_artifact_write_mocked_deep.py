"""Heavy mocked paths in execution_artifact (postgres upsert, mysql upsert, S3, snowflake, BQ, SQL Server, etc.)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import execution_common
import execution_artifact as ea

pytestmark = pytest.mark.unit

_CFG_PG = {"connection_string": "postgresql://u:p@localhost:5432/db"}


class TestPostgresUpsertAndOverwrite:
    def test_upsert_do_update_branch(self, monkeypatch):
        import psycopg2

        class _Cur:
            rowcount = 1

            def execute(self, *_a, **_k):
                return None

            def executemany(self, *_a, **_k):
                raise AssertionError("should use upsert execute, not executemany")

            def close(self):
                pass

        class _Conn:
            def __init__(self):
                self._cur = _Cur()

            def cursor(self):
                return self._cur

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: _Conn())
        out = ea._artifact_write_postgres(
            _CFG_PG,
            {"schema": "public", "table": "items"},
            [{"id": 1, "name": "a"}],
            ["id"],
            "upsert",
            "upsert",
        )
        assert json.loads(out)["mode"] == "upsert"

    def test_upsert_do_nothing_when_only_merge_columns(self, monkeypatch):
        import psycopg2

        class _Cur:
            def execute(self, *_a, **_k):
                return None

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: _Conn())
        out = ea._artifact_write_postgres(
            _CFG_PG,
            {"schema": "public", "table": "items"},
            [{"id": 1}],
            ["id"],
            "upsert",
            "upsert",
        )
        assert "upsert" in out

    def test_upsert_on_conflict_failure_message(self, monkeypatch):
        import psycopg2

        class _Cur:
            def execute(self, *_a, **_k):
                raise RuntimeError("no unique")

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: _Conn())
        out = ea._artifact_write_postgres(
            _CFG_PG,
            {"schema": "public", "table": "items"},
            [{"id": 1, "x": 2}],
            ["id"],
            "upsert",
            "upsert",
        )
        assert "UNIQUE" in out or "PRIMARY" in out

    def test_overwrite_truncate_execute_fails(self, monkeypatch):
        import psycopg2

        class _Cur:
            def execute(self, *_a, **_k):
                raise RuntimeError("denied")

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: _Conn())
        out = ea._artifact_write_postgres(
            _CFG_PG,
            {"schema": "public", "table": "items", "allow_truncate_overwrite": True},
            [{"id": 1}],
            [],
            "append",
            "overwrite",
        )
        assert "TRUNCATE" in out.upper() or "overwrite" in out.lower()

    def test_overwrite_truncate_then_insert(self, monkeypatch):
        import psycopg2

        class _Cur:
            rowcount = 2

            def execute(self, *_a, **_k):
                return None

            def executemany(self, *_a, **_k):
                return None

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(psycopg2, "connect", lambda *_a, **_k: _Conn())
        out = ea._artifact_write_postgres(
            _CFG_PG,
            {"schema": "public", "table": "items", "allow_truncate_overwrite": True},
            [{"id": 1}],
            [],
            "append",
            "overwrite",
        )
        assert json.loads(out)["status"] == "ok"


class TestMysqlUpsertPaths:
    def test_upsert_with_update_non_merge_columns(self, monkeypatch):
        import pymysql

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymysql, "connect", lambda **kw: conn)
        out = ea._artifact_write_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"database": "d", "table": "t"},
            [{"id": 1, "n": "z"}],
            ["id"],
            "upsert",
            "upsert",
        )
        assert json.loads(out)["mode"] == "upsert"
        cur.execute.assert_called_once()

    def test_upsert_merge_only_columns_updates_keys(self, monkeypatch):
        import pymysql

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymysql, "connect", lambda **kw: conn)
        out = ea._artifact_write_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"database": "d", "table": "t"},
            [{"id": 1}],
            ["id"],
            "upsert",
            "upsert",
        )
        assert json.loads(out)["mode"] == "upsert"

    def test_upsert_execute_error_includes_detail(self, monkeypatch):
        import pymysql

        cur = MagicMock()
        cur.execute.side_effect = RuntimeError("no unique index")
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymysql, "connect", lambda **kw: conn)
        out = ea._artifact_write_mysql(
            {"host": "h", "user": "u", "password": "p", "database": "d"},
            {"database": "d", "table": "t"},
            [{"id": 1, "v": 2}],
            ["id"],
            "upsert",
            "upsert",
        )
        assert "UNIQUE" in out or "merge key" in out.lower()


class TestObjectStoreArtifact:
    def test_put_with_append_reads_existing(self, monkeypatch):
        body = MagicMock()
        body.read.return_value = b'{"old":1}\n'
        client = MagicMock()
        client.get_object.return_value = {"Body": body}
        monkeypatch.setattr("boto3.client", lambda *a, **k: client)
        out = ea._artifact_write_object_store(
            "minio",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"bucket": "b", "prefix": "pre"},
            [{"x": 2}],
            b"",
            {"path": "uploads/jobs/j1/out.jsonl", "format": "jsonl"},
            "append",
            "append",
        )
        data = json.loads(out)
        assert data["status"] == "ok"
        client.put_object.assert_called_once()

    def test_missing_bucket_error(self):
        out = ea._artifact_write_object_store(
            "s3",
            {},
            {"prefix": "p"},
            [],
            b"x",
            {"path": "a", "format": "jsonl"},
            "append",
            "append",
        )
        assert "bucket" in out.lower()


class TestSnowflakeArtifactWrite:
    def _install_snowflake_stubs(self, monkeypatch):
        class _Cur:
            def execute(self, *_a, **_k):
                return None

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        class _Cols:
            def __init__(self, keys):
                self._keys = keys

            def tolist(self):
                return list(self._keys)

        class _DF:
            def __init__(self, records):
                self._records = list(records)
                keys = list(self._records[0].keys()) if self._records else []
                self.columns = _Cols(keys)

        pd_mod = types.ModuleType("pandas")
        pd_mod.DataFrame = _DF
        monkeypatch.setitem(sys.modules, "pandas", pd_mod)

        def _wp(_conn, df, **kwargs):
            if kwargs.get("auto_create_table"):
                return (True, 1, len(df._records))
            return (True, 1, len(df._records))

        pkg = types.ModuleType("snowflake")
        conn_mod = types.ModuleType("snowflake.connector")
        conn_mod.connect = MagicMock(return_value=_Conn())
        pkg.connector = conn_mod
        pt = types.ModuleType("snowflake.connector.pandas_tools")
        pt.write_pandas = _wp
        monkeypatch.setitem(sys.modules, "snowflake", pkg)
        monkeypatch.setitem(sys.modules, "snowflake.connector", conn_mod)
        monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", pt)

    def test_append_via_write_pandas(self, monkeypatch):
        self._install_snowflake_stubs(monkeypatch)
        out = ea._artifact_write_snowflake(
            {"user": "u", "password": "p", "account": "acct"},
            {"database": "db", "schema": "public", "table": "t"},
            [{"id": 1}],
            [],
            "append",
            "k1",
        )
        assert json.loads(out)["status"] == "ok"

    def test_merge_via_staging_and_sql(self, monkeypatch):
        self._install_snowflake_stubs(monkeypatch)
        out = ea._artifact_write_snowflake(
            {"user": "u", "password": "p", "account": "acct"},
            {"database": "db", "schema": "public", "table": "t"},
            [{"id": 1, "v": 2}],
            ["id"],
            "upsert",
            "idem",
        )
        assert json.loads(out)["operation"] == "merge"

    def test_fqn_from_three_part_name(self, monkeypatch):
        self._install_snowflake_stubs(monkeypatch)
        out = ea._artifact_write_snowflake(
            {"user": "u", "password": "p", "account": "acct"},
            {"name": 'db.schema.tbl', "table": ""},
            [{"a": 1}],
            [],
            "append",
            "x",
        )
        assert json.loads(out)["status"] == "ok"


class TestBigQueryArtifactBranches:
    def _stub_bq(self, monkeypatch, client: MagicMock):
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        bq = types.ModuleType("google.cloud.bigquery")

        class _WD:
            WRITE_APPEND = "WA"
            WRITE_TRUNCATE = "WT"

        class _CD:
            CREATE_IF_NEEDED = "CI"

        class _SO:
            ALLOW_FIELD_ADDITION = "AFA"

        bq.WriteDisposition = _WD
        bq.CreateDisposition = _CD
        bq.SchemaUpdateOption = _SO
        bq.LoadJobConfig = MagicMock()
        bq.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)
        sa_mod = types.ModuleType("google.oauth2.service_account")

        class _C:
            @staticmethod
            def from_service_account_info(_i):
                return MagicMock()

        sa_mod.Credentials = _C
        monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)

    def test_append_load_json(self, monkeypatch):
        job = MagicMock()
        client = MagicMock()
        client.load_table_from_json.return_value = job
        self._stub_bq(monkeypatch, client)
        out = ea._artifact_write_bigquery(
            {"project_id": "p"},
            {"schema": "ds", "table": "t"},
            [{"id": 1}],
            [],
            "append",
            "append",
        )
        assert json.loads(out)["rows"] == 1

    def test_merge_staging_and_query(self, monkeypatch):
        load_job = MagicMock()
        qjob = MagicMock()
        client = MagicMock()
        client.load_table_from_json.return_value = load_job
        client.query.return_value = qjob
        self._stub_bq(monkeypatch, client)
        out = ea._artifact_write_bigquery(
            {"project_id": "p"},
            {"schema": "ds", "table": "t"},
            [{"id": 1, "v": 2}],
            ["id"],
            "merge",
            "upsert",
        )
        assert json.loads(out)["operation"] == "merge"
        client.delete_table.assert_called()

    def test_validation_errors(self):
        assert "schema" in ea._artifact_write_bigquery({}, {"table": "t"}, [{"a": 1}], [], "append", "append").lower()
        assert "merge_keys" in ea._artifact_write_bigquery(
            {"project_id": "p"},
            {"schema": "d", "table": "t"},
            [{"id": 1}],
            [],
            "upsert",
            "append",
        ).lower()
        assert "missing" in ea._artifact_write_bigquery(
            {"project_id": "p"},
            {"schema": "d", "table": "t"},
            [{"id": 1}],
            ["nope"],
            "upsert",
            "upsert",
        ).lower()


class TestSqlserverArtifactMerge:
    def test_merge_uses_staging(self, monkeypatch):
        import pymssql

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(pymssql, "connect", lambda **kw: conn)
        out = ea._artifact_write_sqlserver(
            {"host": "h", "user": "u", "password": "p", "database": "db"},
            {"schema": "dbo", "table": "Jobs"},
            [{"id": 1, "name": "x"}],
            ["id"],
            "merge",
        )
        assert json.loads(out)["rows"] == 1
        assert cur.execute.call_count >= 3


class TestDatabricksArtifactWrite:
    def test_insert_rows(self, monkeypatch):
        dsql = types.ModuleType("databricks.sql")
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        dsql.connect = MagicMock(return_value=conn)
        pkg = types.ModuleType("databricks")
        pkg.sql = dsql
        monkeypatch.setitem(sys.modules, "databricks", pkg)
        monkeypatch.setitem(sys.modules, "databricks.sql", dsql)
        out = ea._artifact_write_databricks(
            {
                "host": "https://adb.com",
                "token": "t",
                "http_path": "/sql/1.0/warehouses/wh",
            },
            {"schema": "default", "table": "t"},
            [{"id": 1, "v": 2}],
            [],
            "append",
        )
        assert json.loads(out)["rows"] == 1


class TestAzureGcsArtifactWrite:
    def test_azure_upload(self, monkeypatch):
        bc = MagicMock()
        cc = MagicMock()
        cc.get_blob_client.return_value = bc
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        fake = types.ModuleType("azure.storage.blob")
        fake.BlobServiceClient = MagicMock(from_connection_string=MagicMock(return_value=svc))
        monkeypatch.setitem(sys.modules, "azure.storage.blob", fake)
        out = ea._artifact_write_azure_blob(
            {"connection_string": "cs"},
            {"bucket": "c", "prefix": "p"},
            b'{"a":1}\n',
            {"path": "uploads/jobs/x/out.jsonl"},
            "append",
        )
        assert json.loads(out)["status"] == "ok"
        bc.upload_blob.assert_called_once()

    def test_gcs_upload(self, monkeypatch):
        blob = MagicMock()
        bucket = MagicMock()
        bucket.blob.return_value = blob
        client = MagicMock()
        client.bucket.return_value = bucket
        storage_mod = types.ModuleType("google.cloud.storage")
        storage_mod.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
        monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
        sa_mod = types.ModuleType("google.oauth2.service_account")
        sa_mod.Credentials = MagicMock()
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)
        out = ea._artifact_write_gcs_blob(
            {"project_id": "p"},
            {"bucket": "B", "prefix": "pre"},
            b"x",
            {"path": "job/out.jsonl"},
            "append",
        )
        assert json.loads(out)["status"] == "ok"


class TestExecuteArtifactWriteDispatch:
    def _args(self, **kw):
        base = {
            "artifact_ref": {"path": "uploads/jobs/x/y.jsonl", "format": "jsonl", "storage": "local"},
            "operation_type": "append",
            "write_mode": "append",
            "merge_keys": [],
            "idempotency_key": "i1",
        }
        base.update(kw)
        return base

    @patch.object(ea, "_artifact_write_object_store", return_value="s3-ok")
    def test_routes_s3_family(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "s3",
            {"bucket": "b"},
            self._args(
                target={"target_type": "s3", "bucket": "b", "prefix": "p", "table": "ignored"},
            ),
        )
        assert out == "s3-ok"

    @patch.object(ea, "_artifact_write_snowflake", return_value="sf-ok")
    def test_routes_snowflake(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "snowflake",
            {},
            self._args(target={"target_type": "snowflake", "table": "t", "schema": "public"}),
        )
        assert out == "sf-ok"

    @patch.object(ea, "_artifact_write_bigquery", return_value="bq-ok")
    def test_routes_bigquery(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "bigquery",
            {"project_id": "p"},
            self._args(target={"target_type": "bigquery", "schema": "d", "table": "t"}),
        )
        assert out == "bq-ok"

    @patch.object(ea, "_artifact_write_mysql", return_value="my-ok")
    def test_routes_mysql(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "mysql",
            {},
            self._args(target={"target_type": "mysql", "database": "d", "table": "t"}),
        )
        assert out == "my-ok"

    @patch.object(ea, "_artifact_write_databricks", return_value="db-ok")
    def test_routes_databricks(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "databricks",
            {},
            self._args(target={"target_type": "databricks", "table": "t", "schema": "default"}),
        )
        assert out == "db-ok"

    @patch.object(ea, "_artifact_write_azure_blob", return_value="az-ok")
    def test_routes_azure(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "azure_blob",
            {},
            self._args(target={"target_type": "azure_blob", "bucket": "c"}),
        )
        assert out == "az-ok"

    @patch.object(ea, "_artifact_write_gcs_blob", return_value="gcs-ok")
    def test_routes_gcs(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "gcs",
            {},
            self._args(target={"target_type": "gcs", "bucket": "B"}),
        )
        assert out == "gcs-ok"

    @patch.object(ea, "_artifact_write_sqlserver", return_value="ss-ok")
    def test_routes_sqlserver(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "sqlserver",
            {},
            self._args(target={"target_type": "sqlserver", "table": "t", "schema": "dbo"}),
        )
        assert out == "ss-ok"

    @patch.object(ea, "_artifact_write_postgres", return_value="pg-ok")
    def test_routes_postgres_dispatch(self, _m, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda _r: b'{"a":1}\n')
        out = ea.execute_artifact_write(
            "postgres",
            {},
            self._args(target={"target_type": "postgres", "table": "t", "schema": "public"}),
        )
        assert out == "pg-ok"
