"""
Unit tests for platform MCP server tool execution (execute_platform_tool).
Tests each tool type with minimal/invalid config to assert error messages or stub behavior.
No real DBs or external services required.
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Allow importing app from parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import execution
import execution_common
from app import execute_platform_tool


class TestArtifactObjectBasename:
    def test_no_double_jsonl_extension(self):
        assert (
            execution._artifact_object_storage_basename(
                "uploads/jobs/x/job_7_step_1_output.jsonl", ".jsonl"
            ).endswith(".jsonl")
        )
        out = execution._artifact_object_storage_basename("job_7_step_1_output.jsonl", ".jsonl")
        assert out == "job_7_step_1_output.jsonl"
        assert out.count(".jsonl") == 1


class TestPostgres:
    def test_postgres_missing_connection_string(self):
        out = execute_platform_tool("postgres", {}, {"query": "SELECT 1"})
        assert "Error:" in out
        assert "connection_string" in out.lower() or "not configured" in out.lower()

    def test_postgres_missing_query(self):
        out = execute_platform_tool("postgres", {"connection_string": "postgresql://x/y"}, {})
        assert "Error:" in out
        assert "query" in out.lower() and ("not configured" in out.lower() or "required" in out.lower())


class TestMysql:
    def test_mysql_missing_query(self):
        out = execute_platform_tool("mysql", {"host": "localhost", "user": "u", "password": "p", "database": "d"}, {})
        assert "Error:" in out
        assert "query" in out.lower() and ("not configured" in out.lower() or "required" in out.lower())


def _artifact_args(**kwargs):
    base = {
        "artifact_ref": {"path": "uploads/jobs/x", "format": "jsonl"},
        "target": {"schema": "public", "table": "t"},
        "operation_type": "append",
        "write_mode": "append",
        "merge_keys": [],
        "idempotency_key": "k1",
    }
    base.update(kwargs)
    return base


class TestArtifactWriteDatabases:
    """Artifact-first platform writes (output contract path); no real DB required."""

    def test_postgres_artifact_write_missing_connection(self, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda ref: b'{"id":1,"name":"a"}\n')
        out = execution.execute_artifact_write("postgres", {}, _artifact_args())
        assert "connection_string" in out.lower()

    def test_postgres_artifact_write_missing_table(self, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda ref: b'{"id":1}\n')
        args = _artifact_args()
        args["target"] = {"schema": "public"}
        out = execution.execute_artifact_write(
            "postgres",
            {"connection_string": "postgresql://x:y@localhost:5432/db"},
            args,
        )
        assert "target.table" in out.lower() or "table" in out.lower()

    def test_postgres_bootstrap_sql_runs_before_insert(self, monkeypatch):
        from unittest.mock import MagicMock

        import psycopg2

        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda ref: b'{"id":1,"name":"a"}\n')
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        monkeypatch.setattr(psycopg2, "connect", lambda conn_str: mock_conn)

        ddl = 'CREATE TABLE IF NOT EXISTS public.t_job_out ("id" INT, "name" TEXT);'
        args = _artifact_args()
        args["target"] = {
            "schema": "public",
            "table": "t_job_out",
            "bootstrap_sql": ddl,
        }
        out = execution.execute_artifact_write(
            "postgres",
            {"connection_string": "postgresql://u:p@localhost:5432/db"},
            args,
        )
        assert "ok" in out
        assert mock_cur.execute.call_count >= 1
        assert mock_cur.executemany.call_count >= 1
        first_sql = mock_cur.execute.call_args_list[0][0][0]
        assert "CREATE TABLE" in first_sql

    def test_postgres_column_mapping_maps_artifact_keys(self, monkeypatch):
        from unittest.mock import MagicMock

        import psycopg2

        monkeypatch.setattr(
            execution_common,
            "read_artifact_bytes",
            lambda ref: b'{"content": {"a": 1}}\n',
        )
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        monkeypatch.setattr(psycopg2, "connect", lambda conn_str: mock_conn)

        args = _artifact_args()
        args["target"] = {
            "schema": "public",
            "table": "t_out",
            "column_mapping": {"content": "result_json"},
        }
        out = execution.execute_artifact_write(
            "postgres",
            {"connection_string": "postgresql://u:p@localhost:5432/db"},
            args,
        )
        assert "ok" in out
        ins_sql = mock_cur.executemany.call_args[0][0]
        assert "result_json" in ins_sql
        assert "content" not in ins_sql

    def test_postgres_jsonb_columns_wraps_plain_text(self, monkeypatch):
        """Plain text in a JSONB column must use Json() — raw strings are invalid JSON for JSONB."""
        from unittest.mock import MagicMock

        import psycopg2
        from psycopg2.extras import Json

        monkeypatch.setattr(
            execution_common,
            "read_artifact_bytes",
            lambda ref: b'{"content": "The analysis summary text."}\n',
        )
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        monkeypatch.setattr(psycopg2, "connect", lambda conn_str: mock_conn)

        args = _artifact_args()
        args["target"] = {
            "schema": "public",
            "table": "t_out",
            "column_mapping": {"content": "result_json"},
            "jsonb_columns": ["result_json"],
        }
        out = execution.execute_artifact_write(
            "postgres",
            {"connection_string": "postgresql://u:p@localhost:5432/db"},
            args,
        )
        assert "ok" in out
        rows = mock_cur.executemany.call_args[0][1]
        assert isinstance(rows[0][0], Json)

    def test_mysql_artifact_write_missing_database(self, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda ref: b'{"id":1}\n')
        args = _artifact_args()
        args["target"] = {"table": "t"}
        out = execution.execute_artifact_write("mysql", {"host": "localhost", "user": "u", "password": "p"}, args)
        assert "database" in out.lower() and "table" in out.lower()

    def test_bigquery_upsert_without_merge_keys_rejected(self, monkeypatch):
        """Upsert/merge must not fall back to WRITE_TRUNCATE (destructive)."""
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda ref: b'{"id":1}\n')
        args = _artifact_args()
        args["target"] = {"schema": "ds", "table": "t"}
        args["operation_type"] = "upsert"
        args["merge_keys"] = []
        out = execution.execute_artifact_write("bigquery", {"project_id": "p"}, args)
        assert "Error:" in out
        assert "merge_keys" in out.lower()

    def test_bigquery_unsupported_operation_type(self, monkeypatch):
        monkeypatch.setattr(execution_common, "read_artifact_bytes", lambda ref: b'{"id":1}\n')
        args = _artifact_args()
        args["target"] = {"schema": "ds", "table": "t"}
        args["operation_type"] = "truncate"
        out = execution.execute_artifact_write("bigquery", {"project_id": "p"}, args)
        assert "unsupported operation_type" in out.lower()

    def test_databricks_artifact_inserts_all_rows_not_capped_at_5000(self, monkeypatch):
        """Regression: writer must not stop at 5000 rows while reporting len(records)."""
        import execution_artifact as ea

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        sql_mod = types.ModuleType("databricks.sql")
        sql_mod.connect = MagicMock(return_value=mock_conn)
        monkeypatch.setitem(sys.modules, "databricks", types.ModuleType("databricks"))
        monkeypatch.setitem(sys.modules, "databricks.sql", sql_mod)

        n = 6001
        records = [{"id": i, "v": "x"} for i in range(n)]
        out = ea._artifact_write_databricks(
            {"host": "https://x.cloud.databricks.com", "token": "t", "http_path": "/sql/1.0/warehouses/w"},
            {"catalog": "c", "schema": "s", "table": "t"},
            records,
            [],
            "append",
        )
        data = json.loads(out)
        assert data["status"] == "ok"
        assert data["rows"] == n
        assert mock_cur.execute.call_count == n


class TestFilesystem:
    def test_filesystem_missing_base_path(self):
        out = execute_platform_tool("filesystem", {}, {"path": "foo.txt", "action": "read"})
        assert "Error:" in out
        assert "base_path" in out.lower() or "path" in out.lower()

    def test_filesystem_missing_path_argument(self):
        out = execute_platform_tool("filesystem", {"base_path": "/tmp"}, {"action": "read"})
        assert "Error:" in out
        assert "path is required" in out.lower()

    def test_filesystem_read_nonexistent(self, tmp_path):
        out = execute_platform_tool(
            "filesystem",
            {"base_path": str(tmp_path)},
            {"path": "nonexistent.txt", "action": "read"},
        )
        assert "Error:" in out or "not found" in out.lower() or "No such" in out or "read error" in out.lower()

    def test_filesystem_list_directory(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        out = execute_platform_tool(
            "filesystem",
            {"base_path": str(tmp_path)},
            {"path": "subdir", "action": "list"},
        )
        assert "Error:" not in out
        assert isinstance(out, str)
        # Returns newline-separated names (empty for empty dir)
        assert out == "" or "\n" in out or len(out) > 0

    def test_filesystem_write_creates_file(self, tmp_path):
        out = execute_platform_tool(
            "filesystem",
            {"base_path": str(tmp_path)},
            {"path": "out/new.txt", "action": "write", "content": "hello"},
        )
        assert "Error:" not in out
        assert (tmp_path / "out" / "new.txt").read_text() == "hello"


class TestChroma:
    def test_chroma_missing_query(self):
        out = execute_platform_tool("chroma", {"url": "http://localhost:8000", "index_name": "test"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestPinecone:
    def test_pinecone_missing_query(self):
        out = execute_platform_tool("pinecone", {"api_key": "x", "host": "https://x.pinecone.io"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestWeaviate:
    def test_weaviate_missing_query(self):
        out = execute_platform_tool("weaviate", {"url": "http://localhost:8080", "index_name": "Test"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestQdrant:
    def test_qdrant_missing_query(self):
        out = execute_platform_tool("qdrant", {"url": "http://localhost:6333", "index_name": "test"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestVectorDb:
    def test_vector_db_missing_query(self):
        out = execute_platform_tool("vector_db", {"url": "https://x.com", "api_key": "k"}, {"top_k": 5})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestElasticsearch:
    def test_elasticsearch_missing_query(self):
        out = execute_platform_tool("elasticsearch", {"url": "http://localhost:9200"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestRestApi:
    def test_rest_api_missing_path(self):
        out = execute_platform_tool("rest_api", {"base_url": "https://api.example.com"}, {"method": "GET"})
        assert "Error:" in out
        assert "path is required" in out.lower()

    def test_rest_api_with_path_returns_string(self):
        # With invalid host we get error; with valid host we get JSON. Just assert string return.
        out = execute_platform_tool(
            "rest_api",
            {"base_url": "https://invalid-nonexistent-host-xyz.example"},
            {"method": "GET", "path": "/get"},
        )
        assert isinstance(out, str)
        assert "REST API error" in out or "Error:" in out or "status" in out


class TestStubTools:
    """S3 / Slack / GitHub / Notion return clear errors without real credentials (or SDK missing)."""

    def test_s3_without_aws_returns_error(self):
        out = execute_platform_tool("s3", {"bucket": "my-bucket"}, {"key": "x", "action": "get"})
        assert "Error:" in out or "Unable" in out or "NoSuchKey" in out or "not found" in out.lower()

    def test_slack_invalid_token_returns_error(self):
        out = execute_platform_tool("slack", {"bot_token": "xoxb-invalid"}, {"action": "list_channels"})
        assert "Error:" in out

    def test_github_invalid_token_returns_error(self):
        out = execute_platform_tool("github", {"api_key": "ghp_invalid"}, {"repo": "a/b", "path": "README.md", "action": "get_file"})
        assert "Error:" in out

    def test_notion_invalid_key_returns_error(self):
        out = execute_platform_tool("notion", {"api_key": "secret_invalid"}, {"action": "search", "query": "test"})
        assert "Error:" in out


class TestUnknownTool:
    def test_unknown_tool_type(self):
        out = execute_platform_tool("unknown_type", {}, {})
        assert "Unknown tool type" in out
        assert "unknown_type" in out
