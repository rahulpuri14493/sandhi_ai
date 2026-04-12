"""execution_contract: idempotency policy, optional output validation."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from execution_contract import (
    ERROR_IDEMPOTENCY_REQUIRED,
    ERROR_OUTPUT_VALIDATION_FAILED,
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


def test_maybe_validate_output_disabled_pass_through():
    raw = json.dumps({"channels": []})
    assert maybe_validate_messaging_output("slack", "list_channels", raw) == raw


def test_maybe_validate_output_invalid_shape(monkeypatch):
    monkeypatch.setenv("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "true")
    raw = json.dumps({"not_channels": []})
    out = maybe_validate_messaging_output("slack", "list_channels", raw)
    data = json.loads(out)
    assert data.get("error") == ERROR_OUTPUT_VALIDATION_FAILED


def test_tool_error_json_shape():
    s = tool_error_json("validation_failed", "bad")
    assert json.loads(s) == {"error": "validation_failed", "message": "bad"}
