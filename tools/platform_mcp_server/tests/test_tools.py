"""
Unit tests for platform MCP server tool execution (execute_platform_tool).
Tests each tool type with minimal/invalid config to assert error messages or stub behavior.
No real DBs or external services required.
"""
import sys
from pathlib import Path

# Allow importing app from parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import execute_platform_tool


class TestPostgres:
    def test_postgres_missing_connection_string(self):
        out = execute_platform_tool("postgres", {}, {"query": "SELECT 1"})
        assert "Error:" in out
        assert "connection_string" in out.lower() or "not configured" in out.lower()

    def test_postgres_missing_query(self):
        out = execute_platform_tool("postgres", {"connection_string": "postgresql://x/y"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()


class TestMysql:
    def test_mysql_missing_query(self):
        out = execute_platform_tool("mysql", {"host": "localhost", "user": "u", "password": "p", "database": "d"}, {})
        assert "Error:" in out
        assert "query is required" in out.lower()

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
        assert "Error:" in out or "not found" in out.lower() or "No such" in out

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
    """S3, Slack, GitHub, Notion return configured message (no external call in unit test)."""

    def test_s3_configured_message(self):
        out = execute_platform_tool("s3", {"bucket": "my-bucket"}, {"key": "x", "action": "get"})
        assert "S3" in out
        assert "configured" in out.lower() or "boto3" in out.lower()

    def test_slack_configured_message(self):
        out = execute_platform_tool("slack", {"token": "x"}, {"channel": "C1", "message": "hi", "action": "send"})
        assert "Slack" in out
        assert "configured" in out.lower()

    def test_github_configured_message(self):
        out = execute_platform_tool("github", {"token": "x"}, {"repo": "a/b", "path": "README.md", "action": "get_file"})
        assert "GitHub" in out
        assert "configured" in out.lower()

    def test_notion_configured_message(self):
        out = execute_platform_tool("notion", {"api_key": "x"}, {"action": "search", "query": "test"})
        assert "Notion" in out
        assert "configured" in out.lower()


class TestUnknownTool:
    def test_unknown_tool_type(self):
        out = execute_platform_tool("unknown_type", {}, {})
        assert "Unknown tool type" in out
        assert "unknown_type" in out
