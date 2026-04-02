"""Unit tests: execution helpers and app log helpers (no HTTP)."""
import hashlib
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

import execution
import execution_common
from app import _parse_platform_tool_id, _tool_result_for_log
from execution_object_storage import execute_s3_family

pytestmark = pytest.mark.unit


class TestTruncateForLog:
    def test_short_string_unchanged(self):
        assert execution._truncate_for_log("hello", 100) == "hello"

    def test_truncates_and_notes_length(self):
        s = "x" * 3000
        out = execution._truncate_for_log(s, max_len=100)
        assert len(out) < len(s)
        assert "truncated" in out
        assert "total_chars=3000" in out


class TestSqlQueryFromArgs:
    def test_runtime_query_wins_over_config_query_when_readonly(self):
        q = execution_common._sql_query_from_args(
            {"query": "  SELECT 1  "},
            {"query": "SELECT should_be_used"},
        )
        assert q == "SELECT should_be_used"

    def test_reads_nested_statement_from_settings(self):
        q = execution_common._sql_query_from_args(
            {"settings": {"statement": "SELECT 2"}},
            {},
        )
        assert q == "SELECT 2"

    def test_reads_nested_value_object(self):
        q = execution_common._sql_query_from_args(
            {"sql": {"text": "SELECT 3"}},
            {},
        )
        assert q == "SELECT 3"

    def test_reads_runtime_query_when_config_missing(self):
        q = execution_common._sql_query_from_args({}, {"query": "SELECT 1"})
        assert q == "SELECT 1"

    def test_runtime_query_rejects_non_readonly(self):
        with pytest.raises(ValueError, match="read-only"):
            execution_common._sql_query_from_args({}, {"query": "DELETE FROM jobs"})

    @pytest.mark.parametrize(
        "q",
        [
            "SHOW TABLES",
            "SHOW TABLES IN samples.bakehouse",
            "DESCRIBE samples.bakehouse.media_customer_reviews",
            "DESC samples.bakehouse.media_customer_reviews",
            "EXPLAIN SELECT 1",
        ],
    )
    def test_runtime_query_allows_readonly_metadata_statements(self, q):
        # The runtime SQL guard should allow safe metadata introspection statements.
        out = execution_common._sql_query_from_args({}, {"query": q})
        assert out == q


class TestDatabricksReadDetection:
    @pytest.mark.parametrize(
        "q,expect_read",
        [
            ("SELECT 1", True),
            ("WITH x AS (SELECT 1) SELECT * FROM x", True),
            ("SHOW TABLES IN samples.bakehouse", True),
            ("DESCRIBE samples.bakehouse.media_customer_reviews", True),
            ("EXPLAIN SELECT 1", True),
        ],
    )
    def test_databricks_fetches_for_metadata_statements(self, monkeypatch, q, expect_read):
        # Ensure execute_databricks_sql fetches results for SHOW/DESCRIBE/EXPLAIN (not just SELECT/WITH).
        import types
        import sys
        from execution_sql import execute_databricks_sql

        calls: dict = {"fetchall": 0, "commit": 0}

        class _Cur:
            description = [("col",)]

            def execute(self, _query):
                return None

            def fetchall(self):
                calls["fetchall"] += 1
                return [(1,)]

            def close(self):
                return None

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                return None

            def commit(self):
                calls["commit"] += 1
                return None

        sql_mod = types.ModuleType("databricks.sql")
        sql_mod.connect = lambda **_kw: _Conn()
        monkeypatch.setitem(sys.modules, "databricks", types.ModuleType("databricks"))
        monkeypatch.setitem(sys.modules, "databricks.sql", sql_mod)

        out = execute_databricks_sql(
            {"host": "https://x.cloud.databricks.com", "token": "t", "http_path": "/sql/1.0/warehouses/w"},
            {"query": q},
        )
        if expect_read:
            assert calls["fetchall"] >= 1
        assert out

    def test_config_query_used_as_fallback_when_runtime_missing(self):
        q = execution_common._sql_query_from_args(
            {"query": "SELECT from_config"},
            {},
        )
        assert q == "SELECT from_config"


class TestPyMysqlConnectKwargs:
    def test_enables_tls_when_ssl_mode_required(self):
        kw = execution_common._pymysql_connect_kwargs(
            {"host": "db", "user": "u", "password": "p", "database": "d", "ssl_mode": "required"}
        )
        assert "ssl" in kw
        assert kw["ssl"].get("verify_mode") == "none"
        assert kw["ssl"].get("check_hostname") is False

    def test_enables_tls_when_ssl_mode_require_alias(self):
        kw = execution_common._pymysql_connect_kwargs(
            {"host": "db", "user": "u", "password": "p", "database": "d", "ssl_mode": "require"}
        )
        assert "ssl" in kw

    def test_verify_identity_enables_hostname_and_ca_verification(self):
        kw = execution_common._pymysql_connect_kwargs(
            {"host": "db", "user": "u", "password": "p", "database": "d", "ssl_mode": "verify_identity"}
        )
        assert "ssl" in kw
        assert kw["ssl"].get("verify_mode") == "required"
        assert kw["ssl"].get("check_hostname") is True

    def test_does_not_enable_tls_when_disabled(self):
        kw = execution_common._pymysql_connect_kwargs(
            {"host": "db", "user": "u", "password": "p", "database": "d", "ssl_mode": "disabled", "ssl": True}
        )
        assert "ssl" not in kw


class TestRedactObjectStoreKeyForLog:
    def test_empty(self):
        assert execution_common._redact_object_store_key_for_log("") == ""
        assert execution_common._redact_object_store_key_for_log("   ") == ""

    def test_long_key_hides_full_path(self):
        k = "jobs/tenant-uuid/reports/secret-file-name.jsonl"
        out = execution_common._redact_object_store_key_for_log(k)
        assert k not in out
        assert "secret-file" not in out
        assert "jobs" not in out
        assert out == f"len={len(k)} id={hashlib.sha256(k.encode('utf-8')).hexdigest()[:12]}"
        assert len(out) < len(k)

    def test_short_key_no_literal_substrings(self):
        k = "abcdefgh"
        out = execution_common._redact_object_store_key_for_log(k)
        assert k not in out
        assert "len=8" in out
        assert "id=" in out


class TestPostgresDestHint:
    def test_no_password_in_output(self):
        hint = execution._postgres_dest_hint("postgresql://user:secret@db.example.com:5432/mydb")
        assert "secret" not in hint
        assert hint == "db.example.com:5432/mydb"

    def test_malformed_returns_fallback(self):
        assert execution._postgres_dest_hint("") == "postgresql"


class TestToolResultForLog:
    def test_redacts_output_content_and_includes_metadata(self):
        t = 'line1\nline2\t"q"'
        out = _tool_result_for_log(t, max_len=500)
        assert out.startswith("[redacted tool output]")
        assert "len=" in out
        assert "is_error=False" in out
        assert "line1" not in out

    def test_none_returns_redacted_zero_length(self):
        out = _tool_result_for_log(None)  # type: ignore[arg-type]
        assert out == "[redacted tool output] len=0 is_error=False"

    def test_error_prefix_sets_error_flag(self):
        out = _tool_result_for_log("Error: secret details")
        assert "is_error=True" in out


class TestParsePlatformToolId:
    def test_parses_suffix_form(self):
        assert _parse_platform_tool_id("platform_42_postgres") == 42

    def test_parses_minimal(self):
        assert _parse_platform_tool_id("platform_7_") == 7

    def test_rejects_non_platform(self):
        assert _parse_platform_tool_id("other_tool") is None

    def test_rejects_invalid(self):
        assert _parse_platform_tool_id("platform_abc_x") is None


class TestSqlserverToolErrorResponse:
    def test_programming_error_includes_schema_and_default_query_hints(self):
        class ProgrammingError(Exception):
            pass

        msg = execution_common.sqlserver_tool_error_response(
            "SQL Server error",
            ProgrammingError(),
            {"host": "sandhiai.database.windows.net"},
        )
        assert "ProgrammingError" in msg
        assert "column_name" in msg
        assert "default query" in msg
        assert "schema_metadata" in msg

    def test_operational_error_keeps_azure_checklist(self):
        class OperationalError(Exception):
            pass

        msg = execution_common.sqlserver_tool_error_response(
            "SQL Server error",
            OperationalError(),
            {"host": "sandhiai.database.windows.net"},
        )
        assert "OperationalError" in msg
        assert "Azure SQL checklist" in msg


class TestMysqlToolErrorResponse:
    def test_programming_error_includes_mysql_dialect_hints(self):
        class ProgrammingError(Exception):
            pass

        msg = execution_common.mysql_tool_error_response(
            "MySQL error",
            ProgrammingError(),
            {"host": "myserver.mysql.database.azure.com"},
        )
        assert "ProgrammingError" in msg
        assert "LIMIT (not TOP)" in msg
        assert "avoid SQL Server bracket syntax" in msg
        assert "schema_metadata" in msg

    def test_operational_error_includes_tls_and_azure_login_hint(self):
        class OperationalError(Exception):
            pass

        msg = execution_common.mysql_tool_error_response(
            "MySQL error",
            OperationalError(),
            {"host": "myserver.mysql.database.azure.com"},
        )
        assert "OperationalError" in msg
        assert "ssl_mode='required'" in msg
        assert "user@server-name" in msg


class TestResolveLocalArtifactPath:
    def test_rejects_path_with_parent_segments(self, monkeypatch):
        monkeypatch.setattr(execution_common, "_ARTIFACT_ROOT", "/uploads/jobs")
        assert (
            execution_common.resolve_local_artifact_path(
                "uploads/jobs/foo/../../../etc/passwd"
            )
            is None
        )

    def test_resolves_uploads_jobs_under_root(self, monkeypatch, tmp_path):
        root = tmp_path / "jobs"
        root.mkdir()
        f = root / "1" / "a.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("{}\n")
        monkeypatch.setattr(execution_common, "_ARTIFACT_ROOT", str(root))
        p = execution_common.resolve_local_artifact_path("uploads/jobs/1/a.jsonl")
        assert p == str(f.resolve())

    def test_rejects_absolute_outside_root(self, monkeypatch):
        monkeypatch.setattr(execution_common, "_ARTIFACT_ROOT", "/uploads/jobs")
        assert execution_common.resolve_local_artifact_path("/etc/passwd") is None

    def test_read_artifact_rejects_unsafe_s3_key(self):
        with pytest.raises(ValueError, match="unsafe"):
            execution_common.read_artifact_bytes(
                {
                    "storage": "s3",
                    "bucket": "my-bucket",
                    "key": "jobs/../../etc/passwd",
                    "path": "",
                }
            )


class TestLogMcpSql:
    def test_log_mcp_sql_emits_info(self, caplog):
        caplog.set_level(logging.INFO)
        execution._log_mcp_sql("postgres", "SELECT 1", mode="read", dest="localhost:5432/db")
        assert any("MCP SQL" in r.message for r in caplog.records)
        assert any("query_chars=8" in r.message for r in caplog.records)
        assert not any("SELECT 1" in r.message for r in caplog.records)


class TestS3FamilyWritePrefix:
    def test_put_rejected_when_key_not_under_prefix(self, monkeypatch):
        monkeypatch.setenv("MCP_S3_WRITE_KEY_PREFIX", "reports")
        out = execute_s3_family(
            "minio",
            {"bucket": "b"},
            {"action": "put", "key": "total_jobs.txt", "body": b"x"},
        )
        assert "Error" in out
        assert "reports" in out

    def test_put_allowed_when_key_under_prefix(self, monkeypatch):
        monkeypatch.setenv("MCP_S3_WRITE_KEY_PREFIX", "reports")
        fake_client = MagicMock()
        with patch("boto3.client", return_value=fake_client):
            out = execute_s3_family(
                "minio",
                {"bucket": "b", "endpoint": "http://minio:9000"},
                {"action": "put", "key": "reports/job-revenue/out.jsonl", "body": b"{}\n"},
            )
        data = json.loads(out)
        assert data.get("status") == "ok"
        fake_client.put_object.assert_called_once()

    def test_write_prefix_from_tool_config_overrides_env(self, monkeypatch):
        monkeypatch.delenv("MCP_S3_WRITE_KEY_PREFIX", raising=False)
        out = execute_s3_family(
            "minio",
            {"bucket": "b", "write_key_prefix": "reports"},
            {"action": "put", "key": "other.txt", "body": b"x"},
        )
        assert "Error" in out


class TestMergeSqlDialect:
    def test_sqlserver_merge_uses_safe_identifiers(self):
        sql = execution_common._merge_sql_dialect(
            "sqlserver",
            "[dbo].[orders]",
            ["id", "name", "qty"],
            ["id"],
            "#tmp_mcp_" + "a" * 32,
        )
        assert "tgt.[id] = src.[id]" in sql
        assert "[name]" in sql and "[qty]" in sql
        assert "MERGE INTO [dbo].[orders]" in sql

    def test_sqlserver_merge_rejects_bad_column_name(self):
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            execution_common._merge_sql_dialect(
                "sqlserver",
                "[dbo].[t]",
                ["id", "bad;drop"],
                ["id"],
                "#tmp",
            )


class TestSqlserverValidateMergeSql:
    def test_accepts_merge_from_dialect(self):
        sql = execution_common._merge_sql_dialect(
            "sqlserver",
            "[dbo].[orders]",
            ["id", "name", "qty"],
            ["id"],
            "#tmp_mcp_" + "a" * 32,
        )
        execution_common._sqlserver_validate_merge_sql(sql)

    def test_rejects_comment_markers(self):
        base = execution_common._merge_sql_dialect(
            "sqlserver",
            "[dbo].[t]",
            ["id"],
            ["id"],
            "#tmp_mcp_" + "a" * 32,
        )
        with pytest.raises(ValueError, match="comment"):
            execution_common._sqlserver_validate_merge_sql(base.replace("USING", "USING --x"))

    def test_rejects_wrong_shape(self):
        with pytest.raises(ValueError, match="shape"):
            execution_common._sqlserver_validate_merge_sql("SELECT 1")
