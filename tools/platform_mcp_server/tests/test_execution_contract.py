"""execution_contract: idempotency policy, optional output validation."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from execution_contract import (
    ERROR_IDEMPOTENCY_REQUIRED,
    ERROR_OUTPUT_VALIDATION_FAILED,
    allow_writes_without_idempotency_key,
    maybe_validate_messaging_output,
    tool_error_json,
    write_blocked_without_idempotency,
)


def test_write_blocked_without_idempotency():
    blocked = write_blocked_without_idempotency({}, operation="test op")
    assert blocked is not None
    data = json.loads(blocked)
    assert data.get("error") == ERROR_IDEMPOTENCY_REQUIRED
    assert "idempotency_key" in (data.get("message") or "").lower()


def test_write_allowed_with_key():
    assert write_blocked_without_idempotency({"idempotency_key": "k1"}, operation="x") is None


def test_write_allowed_when_env_relax(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY", "true")
    assert write_blocked_without_idempotency({}, operation="x") is None
    assert allow_writes_without_idempotency_key() is True


def test_tool_error_json_skips_none_extra():
    s = tool_error_json("validation_failed", "m", extra=None, keep="v")
    assert json.loads(s) == {"error": "validation_failed", "message": "m", "keep": "v"}


def test_maybe_validate_output_disabled_pass_through():
    raw = json.dumps({"channels": []})
    assert maybe_validate_messaging_output("slack", "list_channels", raw) == raw


def test_maybe_validate_output_invalid_shape(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    raw = json.dumps({"not_channels": []})
    out = maybe_validate_messaging_output("slack", "list_channels", raw)
    data = json.loads(out)
    assert data.get("error") == ERROR_OUTPUT_VALIDATION_FAILED


def test_maybe_validate_valid_slack_channels_and_messages(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    ch = json.dumps({"channels": [{"id": "C1"}]})
    assert maybe_validate_messaging_output("slack", "list_channels", ch) == ch
    msg = json.dumps({"messages": [{"ts": "1"}], "has_more": False})
    assert maybe_validate_messaging_output("slack", "list_messages", msg) == msg


def test_maybe_validate_invalid_messages_array(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    raw = json.dumps({"messages": "nope"})
    out = maybe_validate_messaging_output("slack", "list_messages", raw)
    assert json.loads(out).get("error") == ERROR_OUTPUT_VALIDATION_FAILED


def test_maybe_validate_teams_list_shapes(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    t = json.dumps({"teams": []})
    assert maybe_validate_messaging_output("teams", "list_joined_teams", t) == t
    c = json.dumps({"channels": []})
    assert maybe_validate_messaging_output("teams", "list_channels", c) == c
    bad = json.dumps({"teams": {}})
    assert json.loads(maybe_validate_messaging_output("teams", "list_joined_teams", bad)).get("error") == (
        ERROR_OUTPUT_VALIDATION_FAILED
    )
    bad_ch = json.dumps({"channels": {}})
    assert json.loads(maybe_validate_messaging_output("teams", "list_channels", bad_ch)).get("error") == (
        ERROR_OUTPUT_VALIDATION_FAILED
    )


def test_maybe_validate_pass_through_malformed_json(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    assert maybe_validate_messaging_output("slack", "list_channels", "not-json") == "not-json"


def test_maybe_validate_pass_through_non_object_json(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    assert maybe_validate_messaging_output("slack", "list_channels", "[1,2]") == "[1,2]"


def test_maybe_validate_skips_when_payload_has_error_key(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    raw = json.dumps({"error": "upstream_error", "message": "x"})
    assert maybe_validate_messaging_output("slack", "list_channels", raw) == raw


def test_maybe_validate_unknown_tool_action_noop(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    raw = json.dumps({"x": 1})
    assert maybe_validate_messaging_output("slack", "send", raw) == raw


def test_tool_error_json_shape():
    s = tool_error_json("validation_failed", "bad")
    assert json.loads(s) == {"error": "validation_failed", "message": "bad"}
