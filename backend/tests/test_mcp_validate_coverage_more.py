"""Additional branch coverage for services.mcp_validate (no real external I/O)."""

import smtplib
import socket
import ssl
import sys
import types
from unittest.mock import MagicMock

import pytest

from services import mcp_validate as mv
from services.mcp_validate import validate_tool_config


def test_normalize_http_url_rstrip_empties():
    assert mv._normalize_http_url(")))") == ""


def test_validate_snowflake_missing_user():
    ok, msg = validate_tool_config("snowflake", {"password": "p", "account": "a"})
    assert ok is False and "user" in msg.lower()


def test_validate_snowflake_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "snowflake", types.ModuleType("snowflake"))
    monkeypatch.setitem(sys.modules, "snowflake.connector", types.ModuleType("snowflake.connector"))
    ok, msg = validate_tool_config(
        "snowflake", {"user": "u", "password": "p", "account": "acct"}
    )
    assert ok is False and "snowflake" in msg.lower()


def test_validate_databricks_missing_token():
    ok, msg = validate_tool_config("databricks", {"host": "h", "http_path": "/x"})
    assert ok is False and "token" in msg.lower()


def test_validate_databricks_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "databricks", types.ModuleType("databricks"))
    monkeypatch.setitem(sys.modules, "databricks.sql", types.ModuleType("databricks.sql"))
    ok, msg = validate_tool_config(
        "databricks",
        {"host": "dbc", "token": "t", "http_path": "/sql/1.0/warehouses/abc"},
    )
    assert ok is False and "databricks" in msg.lower()


def test_validate_bigquery_missing_project():
    ok, msg = validate_tool_config("bigquery", {})
    assert ok is False and "project" in msg.lower()


def test_validate_filesystem_missing_path():
    ok, msg = validate_tool_config("filesystem", {})
    assert ok is False and "path" in msg.lower()


def test_validate_filesystem_not_dir(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    ok, msg = validate_tool_config("filesystem", {"base_path": str(f)})
    assert ok is False and "directory" in msg.lower()


def test_validate_filesystem_ok(tmp_path):
    ok, msg = validate_tool_config("filesystem", {"base_path": str(tmp_path)})
    assert ok is True


def test_validate_s3_missing_bucket():
    ok, msg = validate_tool_config("s3", {})
    assert ok is False and "bucket" in msg.lower()


def test_validate_s3_client_list_raises(monkeypatch):
    class FakeClient:
        def list_objects_v2(self, **kw):
            raise RuntimeError("access denied")

    fake_boto = types.ModuleType("boto3")
    fake_boto.client = lambda *a, **kw: FakeClient()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto)
    ok, msg = validate_tool_config("s3", {"bucket": "b"})
    assert ok is False and "unable" in msg.lower()


def test_validate_azure_blob_missing_container():
    ok, msg = validate_tool_config("azure_blob", {"account_url": "https://x.blob.core.windows.net"})
    assert ok is False and "container" in msg.lower()


def test_validate_azure_blob_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.storage", types.ModuleType("azure.storage"))
    monkeypatch.setitem(sys.modules, "azure.storage.blob", types.ModuleType("azure.storage.blob"))
    ok, msg = validate_tool_config(
        "azure_blob",
        {"container": "c", "account_url": "https://x.blob.core.windows.net"},
    )
    assert ok is False and "azure" in msg.lower()


def test_validate_gcs_missing_bucket():
    ok, msg = validate_tool_config("gcs", {})
    assert ok is False and "bucket" in msg.lower()


def test_validate_gcs_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
    monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
    ok, msg = validate_tool_config("gcs", {"bucket": "b"})
    assert ok is False and "gcs" in msg.lower() or "storage" in msg.lower()


def test_validate_slack_missing_token():
    ok, msg = validate_tool_config("slack", {})
    assert ok is False and "token" in msg.lower()


def test_validate_slack_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "slack_sdk", types.ModuleType("slack_sdk"))
    ok, msg = validate_tool_config("slack", {"bot_token": "x"})
    assert ok is False and "slack" in msg.lower()


def test_validate_github_missing_token():
    ok, msg = validate_tool_config("github", {})
    assert ok is False and "token" in msg.lower()


def test_validate_github_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "github", types.ModuleType("github"))
    ok, msg = validate_tool_config("github", {"api_key": "g"})
    assert ok is False and "github" in msg.lower()


def test_validate_notion_missing_key():
    ok, msg = validate_tool_config("notion", {})
    assert ok is False and "key" in msg.lower()


def test_validate_notion_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "notion_client", types.ModuleType("notion_client"))
    ok, msg = validate_tool_config("notion", {"api_key": "n"})
    assert ok is False and "notion" in msg.lower()


def test_validate_rest_api_ok(monkeypatch):
    monkeypatch.setattr(mv, "_http_reachable", lambda url, headers=None, **kwargs: (True, "ok"))
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    ok, _ = validate_tool_config("rest_api", {"url": "https://api.example.com"})
    assert ok is True


def test_validate_pageindex_ok(monkeypatch):
    class R:
        status_code = 200
        text = ""

    monkeypatch.setattr("httpx.get", lambda *a, **kw: R())
    ok, msg = validate_tool_config(
        "pageindex", {"api_key": "k", "base_url": "https://api.pageindex.ai"}
    )
    assert ok is True and "successful" in msg.lower()


def test_validate_pageindex_401(monkeypatch):
    class R:
        status_code = 401
        text = ""

    monkeypatch.setattr("httpx.get", lambda *a, **kw: R())
    ok, msg = validate_tool_config("pageindex", {"api_key": "bad"})
    assert ok is False and "invalid" in msg.lower()


def test_validate_postgres_missing_connection_string():
    ok, msg = validate_tool_config("postgres", {})
    assert ok is False and "connection" in msg.lower()


def test_validate_mysql_import_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "pymysql", raising=False)
    real_import = __import__

    def fake_import(name, *a, **kw):
        if name == "pymysql":
            raise ImportError("no pymysql")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    ok, msg = validate_tool_config(
        "mysql", {"host": "localhost", "user": "u", "password": "p", "database": "d"}
    )
    assert ok is False and "pymysql" in msg.lower()


def test_mysql_connect_kwargs_tls_and_verify_identity():
    cfg = {
        "host": "h",
        "ssl_mode": "verify_identity",
        "ssl_ca": "/ca.pem",
        "user": "u",
        "password": "p",
        "database": "d",
    }
    kw = mv._mysql_connect_kwargs(cfg)
    assert kw["ssl"]["check_hostname"] is True
    assert kw["ssl"]["verify_mode"] == "required"
    assert kw["ssl"]["ca"] == "/ca.pem"


def test_sqlserver_validation_error_login_failed_azure_multi_at():
    msg = mv._sqlserver_validation_error_message(
        Exception("Login failed for user 'a@b@c'"), "myserver.database.windows.net", "a@b@c"
    )
    assert "@" in msg.lower() or "authentication" in msg.lower()


def test_sqlserver_validation_error_certificate():
    msg = mv._sqlserver_validation_error_message(
        Exception("SSL certificate problem"), "localhost", "sa"
    )
    assert "tls" in msg.lower() or "ssl" in msg.lower()


def test_sqlserver_validation_error_connection_refused_azure():
    msg = mv._sqlserver_validation_error_message(
        Exception("connection refused"), "x.database.windows.net", "u@s"
    )
    assert "firewall" in msg.lower() or "unreachable" in msg.lower()


def test_sqlserver_validation_error_timeout_azure():
    msg = mv._sqlserver_validation_error_message(
        Exception("timeout expired"), "x.database.windows.net", "u@s"
    )
    assert "timeout" in msg.lower() or "online" in msg.lower()


def test_github_host_helper_api_github_com():
    assert mv._github_host_is_api_github_com("https://api.github.com") is True
    assert mv._github_host_is_api_github_com("https://enterprise.github.corp/api/v3") is False


def test_validate_elasticsearch_uses_trailing_slash(monkeypatch):
    seen = {}

    def capture(url, headers=None, **kwargs):
        seen["url"] = url
        return True, "ok"

    monkeypatch.setattr(mv, "_http_reachable", capture)
    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    validate_tool_config("elasticsearch", {"url": "https://es.test:9200"})
    assert seen["url"].endswith("/")


def test_validate_pinecone_success(monkeypatch):
    class FakeIdx:
        def describe_index_stats(self):
            return {}

    class FakePC:
        def __init__(self, api_key=None):
            pass

        def Index(self, host=None):
            return FakeIdx()

    pinecone_mod = types.ModuleType("pinecone")
    pinecone_mod.Pinecone = FakePC
    monkeypatch.setitem(sys.modules, "pinecone", pinecone_mod)
    ok, msg = validate_tool_config(
        "pinecone",
        {"api_key": "k", "host": "https://idx.pinecone.io"},
    )
    assert ok is True and "successful" in msg.lower()


def test_validate_mysql_success(monkeypatch):
    class FakeConn:
        def ping(self):
            pass

        def close(self):
            pass

    fake_my = types.ModuleType("pymysql")
    fake_my.connect = lambda **kw: FakeConn()
    monkeypatch.setitem(sys.modules, "pymysql", fake_my)
    ok, msg = validate_tool_config(
        "mysql",
        {
            "host": "localhost",
            "user": "u",
            "password": "p",
            "database": "d",
        },
    )
    assert ok is True and "successful" in msg.lower()


def test_validate_sqlserver_success(monkeypatch):
    class FakeCur:
        def execute(self, q):
            pass

        def fetchone(self):
            return (1,)

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def close(self):
            pass

    fake_ms = types.ModuleType("pymssql")
    fake_ms.connect = lambda **kw: FakeConn()
    monkeypatch.setitem(sys.modules, "pymssql", fake_ms)
    ok, msg = validate_tool_config(
        "sqlserver",
        {"host": "h", "user": "u", "password": "p", "database": "d"},
    )
    assert ok is True and "successful" in msg.lower()


def test_validate_postgres_connection_refused_localhost_message(monkeypatch):
    class Err(Exception):
        pass

    def fake_connect(s):
        raise Err("connection refused")

    fake_pg = types.ModuleType("psycopg2")
    fake_pg.connect = fake_connect
    monkeypatch.setitem(sys.modules, "psycopg2", fake_pg)
    ok, msg = validate_tool_config(
        "postgres", {"connection_string": "postgresql://u:p@localhost:5432/db"}
    )
    assert ok is False
    assert "host.docker.internal" in msg or "docker" in msg.lower()


def test_validate_smtp_gmail_oauth_probes_gmail_rest(monkeypatch):
    """After SMTP 235, Gmail REST is probed so users see whether inbox read will work."""
    calls = {"n": 0}

    class _FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def docmd(self, *a, **k):
            return (235, b"OK")

        def quit(self):
            pass

    def fake_httpx_get(url, **kwargs):
        calls["n"] += 1
        r = MagicMock()
        r.status_code = 200
        return r

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: MagicMock())
    monkeypatch.setattr("httpx.get", fake_httpx_get)

    ok, msg = validate_tool_config(
        "smtp",
        {
            "provider": "gmail",
            "username": "a@gmail.com",
            "access_token": "ya29.unit-test",
            "auth_mode": "oauth2",
        },
    )
    assert ok is True
    assert "Gmail REST" in msg
    assert calls["n"] == 1
