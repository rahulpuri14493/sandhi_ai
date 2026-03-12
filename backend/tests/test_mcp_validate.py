"""Unit tests for MCP tool config validation (no DB, no real connections).
Positive: valid inputs that should pass.
Negative: invalid/missing inputs that should fail with clear messages.
"""
import pytest

from services.mcp_validate import validate_tool_config


# ---------- Positive test cases (valid inputs, expect success or skip) ----------


class TestValidateToolConfigPositive:
    """Positive: valid configs return valid=True or acceptable skip message."""

    def test_positive_filesystem_valid_path(self, tmp_path):
        valid, msg = validate_tool_config("filesystem", {"base_path": str(tmp_path)})
        assert valid is True
        assert "exists" in msg.lower() or "readable" in msg.lower()

    def test_positive_vector_db_returns_skip_message(self):
        valid, msg = validate_tool_config("vector_db", {})
        assert valid is True
        assert "not available" in msg or "save to store" in msg.lower()

    def test_positive_pinecone_no_validation_succeeds(self):
        valid, msg = validate_tool_config("pinecone", {})
        assert valid is True

    def test_positive_slack_no_validation_succeeds(self):
        valid, msg = validate_tool_config("slack", {})
        assert valid is True

    def test_positive_rest_api_accepts_url_key(self):
        # Still fails without real URL; we test that tool_type is accepted
        valid, msg = validate_tool_config("rest_api", {"base_url": ""})
        assert valid is False
        assert "URL is required" in msg


# ---------- Negative test cases (invalid inputs, expect valid=False or error) ----------


class TestValidateToolConfigNegative:
    """validate_tool_config returns (valid, message) per tool type."""

    """Negative: invalid or missing config returns valid=False with clear message."""

    def test_negative_postgres_missing_connection_string(self):
        valid, msg = validate_tool_config("postgres", {})
        assert valid is False
        assert "Connection string is required" in msg

    def test_negative_postgres_empty_connection_string(self):
        valid, msg = validate_tool_config("postgres", {"connection_string": "   "})
        assert valid is False
        assert "Connection string is required" in msg

    def test_negative_mysql_empty_config(self):
        valid, msg = validate_tool_config("mysql", {})
        assert valid is False
        assert msg

    def test_negative_filesystem_missing_base_path(self):
        valid, msg = validate_tool_config("filesystem", {})
        assert valid is False
        assert "Base path is required" in msg

    def test_negative_filesystem_empty_base_path(self):
        valid, msg = validate_tool_config("filesystem", {"base_path": "   "})
        assert valid is False
        assert "Base path is required" in msg

    def test_negative_filesystem_nonexistent_path(self):
        valid, msg = validate_tool_config("filesystem", {"base_path": "/nonexistent/path/xyz"})
        assert valid is False
        assert "not a directory" in msg or "does not exist" in msg

    def test_negative_elasticsearch_missing_url(self):
        valid, msg = validate_tool_config("elasticsearch", {})
        assert valid is False
        assert "URL is required" in msg

    def test_negative_rest_api_missing_url(self):
        valid, msg = validate_tool_config("rest_api", {})
        assert valid is False
        assert "URL is required" in msg


# ---------- All MCP tool types: validation behavior ----------


class TestValidateToolConfigAllTypes:
    """Every MCPToolType has defined behavior (valid + message or valid=False)."""

    def test_chroma_returns_skip_message(self):
        valid, msg = validate_tool_config("chroma", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower() or "credentials" in msg.lower()

    def test_pinecone_accepts_without_validation(self):
        valid, msg = validate_tool_config("pinecone", {})
        assert valid is True

    def test_weaviate_returns_skip_message(self):
        valid, msg = validate_tool_config("weaviate", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower() or "credentials" in msg.lower()

    def test_qdrant_returns_skip_message(self):
        valid, msg = validate_tool_config("qdrant", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower() or "credentials" in msg.lower()

    def test_vector_db_returns_skip_message(self):
        valid, msg = validate_tool_config("vector_db", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower()

    def test_s3_returns_skip_message(self):
        valid, msg = validate_tool_config("s3", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower() or "credentials" in msg.lower()

    def test_github_returns_skip_message(self):
        valid, msg = validate_tool_config("github", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower() or "credentials" in msg.lower()

    def test_notion_returns_skip_message(self):
        valid, msg = validate_tool_config("notion", {})
        assert valid is True
        assert "not available" in msg or "save" in msg.lower() or "store" in msg.lower() or "credentials" in msg.lower()

    def test_rest_api_empty_url_fails(self):
        valid, msg = validate_tool_config("rest_api", {"base_url": ""})
        assert valid is False
        assert "URL is required" in msg
