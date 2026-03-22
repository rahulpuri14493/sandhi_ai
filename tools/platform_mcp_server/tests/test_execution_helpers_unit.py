"""Unit tests: execution helpers and app log helpers (no HTTP)."""
import logging

import pytest

import execution
from app import _parse_platform_tool_id, _tool_result_for_log

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


class TestPostgresDestHint:
    def test_no_password_in_output(self):
        hint = execution._postgres_dest_hint("postgresql://user:secret@db.example.com:5432/mydb")
        assert "secret" not in hint
        assert "db.example.com" in hint
        assert "5432" in hint
        assert "mydb" in hint

    def test_malformed_returns_fallback(self):
        assert execution._postgres_dest_hint("") == "postgresql"


class TestToolResultForLog:
    def test_escapes_newlines_and_tabs(self):
        t = 'line1\nline2\t"q"'
        out = _tool_result_for_log(t, max_len=500)
        assert "\\n" in out
        assert "\\t" in out
        assert "\n" not in out or out.count("\n") <= 1  # single log line

    def test_none_returns_empty(self):
        assert _tool_result_for_log(None) == ""  # type: ignore[arg-type]


class TestParsePlatformToolId:
    def test_parses_suffix_form(self):
        assert _parse_platform_tool_id("platform_42_postgres") == 42

    def test_parses_minimal(self):
        assert _parse_platform_tool_id("platform_7_") == 7

    def test_rejects_non_platform(self):
        assert _parse_platform_tool_id("other_tool") is None

    def test_rejects_invalid(self):
        assert _parse_platform_tool_id("platform_abc_x") is None


class TestLogMcpSql:
    def test_log_mcp_sql_emits_info(self, caplog):
        caplog.set_level(logging.INFO)
        execution._log_mcp_sql("postgres", "SELECT 1", mode="read", dest="localhost:5432/db")
        assert any("MCP SQL" in r.message for r in caplog.records)
        assert any("SELECT 1" in r.message for r in caplog.records)
