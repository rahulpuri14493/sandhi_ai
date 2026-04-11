"""Deep unit tests for execution_artifact helpers and execute_artifact_write branches (mocked I/O)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import execution_artifact as ea
import execution_common

pytestmark = pytest.mark.unit


class TestRedactTargetForLog:
    def test_non_dict(self):
        out = ea._redact_target_for_log("x")  # type: ignore[arg-type]
        assert out["target_redacted"] is True
        assert out["keys"] == []

    def test_skips_sensitive_and_keeps_safe(self):
        out = ea._redact_target_for_log(
            {
                "target_type": "postgres",
                "table": "t",
                "api_password": "secret",
                "nested": {"a": 1},
            }
        )
        assert "api_password" not in out
        assert out.get("table") == "t"
        assert "nested" not in out


class TestIsSafeBootstrapSql:
    @pytest.mark.parametrize(
        "stmt,ok",
        [
            ("", False),
            ("  ", False),
            ("-- hi", False),
            ("/*x*/ create table a (b int)", False),
            ("drop table a", False),
            ("truncate table a", False),
            ("create table a (b int)", True),
            ("CREATE INDEX i ON a (b)", True),
            ("alter table a add column c int", True),
            ("comment on table a is 'x'", True),
            ("delete from a", False),
        ],
    )
    def test_allowlist(self, stmt: str, ok: bool):
        assert ea._is_safe_bootstrap_sql(stmt) is ok

    def test_multiple_allowed_statements(self):
        assert ea._is_safe_bootstrap_sql("create table a (b int); create index i on a (b)") is True


class TestTrustedBootstrapAndSignedRuntime:
    def test_signature_roundtrip(self):
        sig = ea._trusted_bootstrap_signature(
            tool_name="mytool",
            operation_type="append",
            schema="public",
            table="jobs",
            bootstrap_sql="create table jobs (id int)",
            secret="s3cr3t",
        )
        assert len(sig) == 64

    def test_signed_runtime_accepts_valid_sig(self, monkeypatch):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "sec")
        boot = "create table t (id int)"
        sig = ea._trusted_bootstrap_signature(
            tool_name="t1",
            operation_type="append",
            schema="s",
            table="tbl",
            bootstrap_sql=boot,
            secret="sec",
        )
        args = {
            "tool_name": "t1",
            "operation_type": "append",
            "trusted_bootstrap": {"bootstrap_sql": boot, "sig": sig},
        }
        rt = {"schema": "s", "table": "tbl"}
        assert ea._bootstrap_sql_from_signed_runtime(args, rt, "append") == boot

    def test_signed_runtime_rejects_bad_sig(self, monkeypatch):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "sec")
        args = {
            "tool_name": "t1",
            "operation_type": "append",
            "trusted_bootstrap": {"bootstrap_sql": "x", "sig": "deadbeef"},
        }
        assert ea._bootstrap_sql_from_signed_runtime(args, {"schema": "s", "table": "tbl"}, "append") is None

    def test_no_secret_no_bootstrap(self, monkeypatch):
        monkeypatch.delenv("MCP_INTERNAL_SECRET", raising=False)
        assert ea._bootstrap_sql_from_signed_runtime({}, {}, "append") is None


class TestPostgresHelpers:
    def test_apply_column_mapping_and_jsonb(self):
        recs = [{"a": 1, "b": 2}]
        mapped = ea._apply_postgres_column_mapping(recs, {"column_mapping": {"a": "alpha"}})
        assert mapped[0] == {"alpha": 1, "b": 2}

    def test_apply_mapping_skips_non_dict_rows(self):
        out = ea._apply_postgres_column_mapping([{"x": 1}, "skip", {"y": 2}], {"column_mapping": {"x": "z"}})
        assert out[0] == {"z": 1}
        assert out[1] == "skip"

    def test_jsonb_column_set_variants(self):
        assert ea._jsonb_column_set({}) == set()
        assert ea._jsonb_column_set({"jsonb_columns": ["a", "b"]}) == {"a", "b"}
        assert ea._jsonb_column_set({"json_columns": "meta"}) == {"meta"}

    def test_postgres_adapt_cell_jsonb(self):
        class JsonShim:
            def __init__(self, v):
                self.v = v

        fake_extras = MagicMock()
        fake_extras.Json = JsonShim
        fake_pg = MagicMock()
        fake_pg.extras = fake_extras
        with patch.dict(sys.modules, {"psycopg2": fake_pg, "psycopg2.extras": fake_extras}):
            cell = ea._postgres_adapt_cell("j", {"k": 1}, {"j"})
        assert isinstance(cell, JsonShim)

    def test_postgres_run_bootstrap_success_and_reject(self):
        cur = MagicMock()
        conn = MagicMock()
        assert ea._postgres_run_bootstrap_sql(cur, conn, {}) is None
        err = ea._postgres_run_bootstrap_sql(cur, conn, {"bootstrap_sql": 123})  # type: ignore[dict-item]
        assert "must be a string" in (err or "")
        err2 = ea._postgres_run_bootstrap_sql(cur, conn, {"bootstrap_sql": "drop table x"})
        assert "rejected" in (err2 or "")
        assert ea._postgres_run_bootstrap_sql(cur, conn, {"bootstrap_sql": "create table x (a int)"}) is None
        cur.execute.assert_called()
        conn.commit.assert_called()

    def test_mysql_snowflake_bootstrap_bad_type(self):
        cur = MagicMock()
        conn = MagicMock()
        for fn in (ea._mysql_run_bootstrap_sql, ea._snowflake_run_bootstrap_sql):
            e = fn(cur, conn, {"bootstrap_sql": {}})  # type: ignore[dict-item]
            assert e and "must be a string" in e


class TestBigQueryHelpers:
    def test_fq_table_and_ref(self):
        assert ea._bq_fq_table("p", "d", "t") == "`p.d.t`"
        assert ea._bq_fq_table("", "d", "t") == "`d.t`"
        assert ea._bq_table_ref("p", "d", "t") == "p.d.t"
        assert ea._bq_table_ref("", "d", "t") == "d.t"


class TestBigqueryBootstrap:
    def test_bigquery_bootstrap_calls_client(self):
        job = MagicMock()
        client = MagicMock()
        client.query.return_value = job
        assert ea._bigquery_run_bootstrap_sql(client, {}) is None
        assert ea._bigquery_run_bootstrap_sql(client, {"bootstrap_sql": "create table x (a int)"}) is None
        client.query.assert_called()
        job.result.assert_called()

    def test_bigquery_bootstrap_query_error(self):
        client = MagicMock()
        client.query.return_value.result.side_effect = RuntimeError("boom")
        err = ea._bigquery_run_bootstrap_sql(client, {"bootstrap_sql": "create table x (a int)"})
        assert err and "bootstrap" in err.lower()


class TestDatabricksBootstrap:
    def test_databricks_bootstrap(self):
        cur = MagicMock()
        conn = MagicMock()
        assert ea._databricks_run_bootstrap_sql(cur, conn, {"bootstrap_sql": "create table x (a int)"}) is None
        cur.execute.assert_called()
        conn.commit.assert_called()


class TestSqlserverBootstrap:
    def test_sqlserver_bootstrap_error_uses_sqlserver_response(self):
        cur = MagicMock()
        cur.execute.side_effect = RuntimeError("nope")
        conn = MagicMock()
        err = ea._sqlserver_run_bootstrap_sql(cur, conn, {"bootstrap_sql": "create table x (a int)"}, {"host": "h"})
        assert err and "Error" in err


class TestExecuteArtifactWriteBranches:
    def _base_args(self, **kw):
        base = {
            "artifact_ref": {"path": "p", "format": "jsonl", "storage": "local"},
            "target": {"target_type": "postgres", "schema": "public", "table": "t"},
            "operation_type": "append",
            "write_mode": "append",
            "merge_keys": [],
            "idempotency_key": "idem-1",
        }
        base.update(kw)
        return base

    @patch.object(ea, "_artifact_write_postgres", return_value="postgres-ok")
    def test_dispatches_postgres_with_records(self, _mock_pg, monkeypatch):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "")
        with patch.object(execution_common, "read_artifact_bytes", return_value=b'{"id": 1}\n'):
            out = ea.execute_artifact_write("postgres", {"connection_string": "postgresql://x"}, self._base_args())
        assert out == "postgres-ok"

    def test_no_records_error(self, monkeypatch):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "")
        with patch.object(execution_common, "read_artifact_bytes", return_value=b""):
            out = ea.execute_artifact_write("postgres", {}, self._base_args())
        assert "no records" in out.lower()

    def test_parse_error(self, monkeypatch):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "")
        with patch.object(execution_common, "read_artifact_bytes", return_value=b"not-json\n"):
            out = ea.execute_artifact_write("postgres", {}, self._base_args())
        assert "could not parse" in out.lower()

    def test_unimplemented_tool(self, monkeypatch):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "")
        with patch.object(execution_common, "read_artifact_bytes", return_value=b'{"a":1}\n'):
            out = ea.execute_artifact_write(
                "unknown_tool",
                {},
                self._base_args(target={"target_type": "unknown_tool", "table": "x"}),
            )
        assert "not implemented" in out.lower()

    @patch.object(ea, "_artifact_write_postgres", return_value="ok")
    def test_runtime_bootstrap_sql_ignored_with_warning(self, _mock, monkeypatch, caplog):
        monkeypatch.setenv("MCP_INTERNAL_SECRET", "")
        import logging

        caplog.set_level(logging.WARNING)
        args = self._base_args(
            target={"target_type": "postgres", "schema": "public", "table": "t", "bootstrap_sql": "evil"}
        )
        with patch.object(execution_common, "read_artifact_bytes", return_value=b'{"id":1}\n'):
            ea.execute_artifact_write("postgres", {}, args)
        assert any("Ignoring user-supplied target.bootstrap_sql" in r.message for r in caplog.records)
