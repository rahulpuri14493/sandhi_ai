"""Unit tests for MCP tool config validation (deterministic, no real network)."""

import sys
import types
from unittest.mock import patch

import pytest

from services.mcp_validate import _mysql_connect_kwargs, validate_tool_config


class TestValidateToolConfigPositive:
    def test_filesystem_valid_path(self, tmp_path):
        valid, msg = validate_tool_config("filesystem", {"base_path": str(tmp_path)})
        assert valid is True
        assert "exists" in msg.lower() or "readable" in msg.lower()


class TestValidateToolConfigNegative:
    def test_postgres_missing_connection_string(self):
        valid, msg = validate_tool_config("postgres", {})
        assert valid is False
        assert "connection string is required" in msg.lower()

    def test_filesystem_missing_base_path(self):
        valid, msg = validate_tool_config("filesystem", {})
        assert valid is False
        assert "base path is required" in msg.lower()

    def test_filesystem_nonexistent_path(self):
        valid, msg = validate_tool_config("filesystem", {"base_path": "/nonexistent/path/xyz"})
        assert valid is False
        assert "not a directory" in msg.lower() or "does not exist" in msg.lower()

    def test_rest_api_missing_url(self):
        valid, msg = validate_tool_config("rest_api", {})
        assert valid is False
        assert "url is required" in msg.lower()

    def test_mysql_require_secure_transport_hint(self):
        class _FakePyMysql:
            @staticmethod
            def connect(**_kw):
                raise Exception("Connections using insecure transport are prohibited while --require_secure_transport=ON.")

        with patch.dict(sys.modules, {"pymysql": _FakePyMysql}):
            valid, msg = validate_tool_config(
                "mysql",
                {"host": "mysql.example.com", "database": "sales", "user": "u", "password": "p"},
            )
        assert valid is False
        assert "secure transport" in msg.lower()
        assert "ssl_mode='required'" in msg.lower()


class TestMySqlConnectKwargs:
    def test_required_ssl_mode_builds_truthy_ssl_dict(self):
        kw = _mysql_connect_kwargs(
            {"host": "db", "user": "u", "password": "p", "database": "d", "ssl_mode": "required"}
        )
        assert "ssl" in kw
        assert kw["ssl"].get("verify_mode") == "none"
        assert kw["ssl"].get("check_hostname") is False


@pytest.mark.parametrize(
    "tool_type,config,expected_fragment",
    [
        ("vector_db", {}, "url is required"),
        ("pinecone", {}, "api key is required"),
        ("weaviate", {}, "url is required"),
        ("qdrant", {}, "url is required"),
        ("chroma", {}, "url is required"),
        ("sqlserver", {"host": "x", "database": "d", "password": "p"}, "user is required"),
        ("snowflake", {}, "user is required"),
        ("databricks", {}, "host is required"),
        ("bigquery", {}, "project_id is required"),
        ("elasticsearch", {}, "url is required"),
        ("pageindex", {}, "api key is required"),
        ("s3", {}, "bucket is required"),
        ("minio", {}, "bucket is required"),
        ("ceph", {}, "bucket is required"),
        ("azure_blob", {}, "container is required"),
        ("gcs", {}, "bucket is required"),
        ("slack", {}, "token is required"),
        ("github", {}, "token is required"),
        ("notion", {}, "api key is required"),
        ("rest_api", {}, "url is required"),
    ],
)
def test_all_tool_types_have_explicit_validation(tool_type, config, expected_fragment):
    valid, msg = validate_tool_config(tool_type, config)
    assert valid is False
    assert expected_fragment in msg.lower()


class TestSqlServerValidationHints:
    @staticmethod
    def _sqlserver_base_cfg(user: str = "admin@sandhiai") -> dict:
        return {
            "host": "sandhiai.database.windows.net",
            "port": 1433,
            "database": "free-sql-db-1732366",
            "user": user,
            "password": "secret",
        }

    def test_sqlserver_login_failed_hint(self):
        class _FakePyMssql:
            @staticmethod
            def connect(**_kw):
                raise Exception("Login failed for user 'x' (18456)")

        with patch.dict(sys.modules, {"pymssql": _FakePyMssql}):
            valid, msg = validate_tool_config("sqlserver", self._sqlserver_base_cfg())
        assert valid is False
        assert "authentication failed" in msg.lower()
        assert "admin@sandhiai" in msg.lower() or "logical-server" in msg.lower()

    def test_sqlserver_tls_hint(self):
        class _FakePyMssql:
            @staticmethod
            def connect(**_kw):
                raise Exception("SSL Provider: handshake failure")

        with patch.dict(sys.modules, {"pymssql": _FakePyMssql}):
            valid, msg = validate_tool_config("sqlserver", self._sqlserver_base_cfg())
        assert valid is False
        assert "tls/ssl handshake failed" in msg.lower()

    def test_sqlserver_bad_user_format_multiple_at_hint(self):
        class _FakePyMssql:
            @staticmethod
            def connect(**_kw):
                raise Exception("Login failed for user")

        with patch.dict(sys.modules, {"pymssql": _FakePyMssql}):
            valid, msg = validate_tool_config(
                "sqlserver",
                self._sqlserver_base_cfg(user="rahul149386@outlook.com@sandhiai"),
            )
        assert valid is False
        assert "multiple '@'" in msg.lower()
