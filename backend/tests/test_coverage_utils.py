"""Targeted unit tests to boost overall coverage (no external services)."""

import types

import pytest


def test_format_schema_for_prompt_formats_and_truncates():
    from services.db_schema_introspection import format_schema_for_prompt

    schema = {
        "tables": [
            {
                "name": "users",
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "email", "type": "text"},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
            },
            {
                "name": "jobs",
                "columns": [{"name": "business_id", "type": "integer"}],
                "primary_key": [],
                "foreign_keys": [
                    {
                        "columns": ["business_id"],
                        "references_table": "users",
                        "references_columns": ["id"],
                    }
                ],
            },
        ]
    }
    out = format_schema_for_prompt(schema, max_chars=10_000)
    assert "Table users" in out
    assert "Primary key: id" in out
    assert "Foreign key:" in out

    truncated = format_schema_for_prompt(schema, max_chars=20)
    assert truncated.endswith("(schema truncated)")


def test_introspect_sql_tool_unsupported_type():
    from services.db_schema_introspection import introspect_sql_tool

    schema, err = introspect_sql_tool("sqlite", {})
    assert schema is None
    assert "not supported" in (err or "").lower()


def test_validate_tool_config_filesystem(tmp_path):
    from services.mcp_validate import validate_tool_config

    ok, msg = validate_tool_config("filesystem", {"base_path": str(tmp_path)})
    assert ok is True
    assert "readable" in msg.lower()

    ok, msg = validate_tool_config("filesystem", {"base_path": str(tmp_path / "missing")})
    assert ok is False
    assert "does not exist" in msg.lower()


def test_validate_tool_config_http(monkeypatch):
    from services import mcp_validate
    import httpx

    class R:
        def __init__(self, code: int, text: str = "ok"):
            self.status_code = code
            self.text = text

    import socket

    monkeypatch.setattr(
        "services.http_url_guard.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))],
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(200, "ok"))
    ok, msg = mcp_validate.validate_tool_config("rest_api", {"url": "example.com"})
    assert ok is True
    assert "reachable" in msg.lower()

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(503, "down"))
    ok, msg = mcp_validate.validate_tool_config("elasticsearch", {"url": "https://example.com"})
    assert ok is False
    assert "503" in msg


def test_validate_tool_config_postgres_localhost_hint(monkeypatch):
    from services import mcp_validate

    # Fake psycopg2 module that always raises "connection refused"
    fake_psycopg2 = types.SimpleNamespace(connect=lambda *a, **k: (_ for _ in ()).throw(Exception("Connection refused")))
    monkeypatch.setitem(__import__("sys").modules, "psycopg2", fake_psycopg2)

    ok, msg = mcp_validate.validate_tool_config(
        "postgres",
        {"connection_string": "postgresql://postgres:postgres@localhost:5432/agent_marketplace"},
    )
    assert ok is False
    assert "host.docker.internal" in msg or "db:5432" in msg


def test_a2a_extract_result_message_and_task_paths():
    from services.a2a_client import _extract_result_from_send_message_response

    # Direct message path
    body = {"result": {"message": {"parts": [{"text": "hi"}]}}}
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "hi"

    # Task completed with artifacts path
    body = {
        "result": {
            "task": {
                "id": "t1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"text": "done"}]}],
            }
        }
    }
    out = _extract_result_from_send_message_response(body)
    assert out["content"] == "done"
    assert out["task_id"] == "t1"

    # Task failed path
    body = {
        "result": {
            "task": {
                "id": "t2",
                "status": {"state": "TASK_STATE_FAILED", "message": {"parts": [{"text": "nope"}]}},
            }
        }
    }
    with pytest.raises(Exception, match="TASK_STATE_FAILED"):
        _extract_result_from_send_message_response(body)


def test_a2a_validate_public_http_url_blocks_private(monkeypatch):
    from services import a2a_client

    monkeypatch.setattr(a2a_client.settings, "ALLOW_PRIVATE_AGENT_ENDPOINTS", False)
    monkeypatch.setattr(a2a_client.socket, "getaddrinfo", lambda *a, **k: [(a2a_client.socket.AF_INET, None, None, None, ("127.0.0.1", 0))])

    with pytest.raises(ValueError, match="public IP"):
        a2a_client._validate_public_http_url("http://example.local")

    monkeypatch.setattr(a2a_client.settings, "ALLOW_PRIVATE_AGENT_ENDPOINTS", True)
    assert a2a_client._validate_public_http_url("http://example.local").startswith("http")


def test_run_alembic_upgrade_postgres_path(monkeypatch, tmp_path):
    # Cover most of db/run_alembic_upgrade without real Postgres.
    import db.run_alembic_upgrade as mod

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *args, **kwargs):
            # Return object with scalar() method.
            class R:
                def __init__(self, v):
                    self._v = v

                def scalar(self):
                    return self._v

            sql = str(args[0])
            # Pretend alembic_version table does not exist but app tables do.
            if "table_name = 'alembic_version'" in sql:
                return R(None)
            if "table_name IN ('users', 'agents', 'jobs')" in sql:
                return R(1)
            return R(None)

    class FakeDialect:
        name = "postgresql"

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return FakeConn()

    monkeypatch.setattr(mod, "engine", FakeEngine())
    monkeypatch.setattr(mod.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(mod.os, "chdir", lambda p: None)

    # Fake Alembic modules
    calls = {"stamp": 0, "upgrade": 0}

    class FakeCommand:
        @staticmethod
        def stamp(cfg, rev):
            calls["stamp"] += 1

        @staticmethod
        def upgrade(cfg, rev):
            calls["upgrade"] += 1

    class FakeConfig:
        def __init__(self, path):
            self.path = path

        def set_main_option(self, *a, **k):
            return None

    import sys
    import types as pytypes

    alembic_mod = pytypes.ModuleType("alembic")
    command_mod = pytypes.ModuleType("alembic.command")
    command_mod.stamp = FakeCommand.stamp
    command_mod.upgrade = FakeCommand.upgrade
    config_mod = pytypes.ModuleType("alembic.config")
    config_mod.Config = FakeConfig
    alembic_mod.command = command_mod

    monkeypatch.setitem(sys.modules, "alembic", alembic_mod)
    monkeypatch.setitem(sys.modules, "alembic.command", command_mod)
    monkeypatch.setitem(sys.modules, "alembic.config", config_mod)

    # run
    mod.run_alembic_upgrade()
    assert calls["stamp"] == 1
    assert calls["upgrade"] == 1

