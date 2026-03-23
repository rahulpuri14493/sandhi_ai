"""Unit tests: execution helpers and app log helpers (no HTTP)."""
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


class TestRedactObjectStoreKeyForLog:
    def test_empty(self):
        assert execution_common._redact_object_store_key_for_log("") == ""
        assert execution_common._redact_object_store_key_for_log("   ") == ""

    def test_long_key_hides_full_path(self):
        k = "jobs/tenant-uuid/reports/secret-file-name.jsonl"
        out = execution_common._redact_object_store_key_for_log(k)
        assert k not in out
        assert "secret-file" not in out
        assert "len=" in out
        assert "id=" in out
        assert len(out) < len(k)

    def test_short_key_masks(self):
        out = execution_common._redact_object_store_key_for_log("abcdefgh")
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
            "#tmp_mcp_1",
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
