"""Unified MCP messaging contract: error codes, idempotency policy, optional output checks (Issue #71)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

# --- Unified Sandhi / platform MCP error codes (JSON tool results) ---
ERROR_VALIDATION_FAILED = "validation_failed"
ERROR_AUTH_FAILED = "auth_failed"
ERROR_PERMISSION_DENIED = "permission_denied"
ERROR_UPSTREAM_ERROR = "upstream_error"
ERROR_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
ERROR_IDEMPOTENCY_REQUIRED = "idempotency_required"
ERROR_OUTPUT_VALIDATION_FAILED = "output_validation_failed"
ERROR_CONFIGURATION_ERROR = "configuration_error"
ERROR_UNKNOWN_ACTION = "unknown_action"

CONTRACT_ERROR_CODES = frozenset(
    {
        ERROR_VALIDATION_FAILED,
        ERROR_AUTH_FAILED,
        ERROR_PERMISSION_DENIED,
        ERROR_UPSTREAM_ERROR,
        ERROR_UPSTREAM_UNAVAILABLE,
        ERROR_IDEMPOTENCY_REQUIRED,
        ERROR_OUTPUT_VALIDATION_FAILED,
        ERROR_CONFIGURATION_ERROR,
        ERROR_UNKNOWN_ACTION,
        "graph_api_error",  # legacy Graph payloads (deprecated; prefer permission_denied / auth_failed)
    }
)


def allow_writes_without_idempotency_key() -> bool:
    """When false (default), send/send_message/reply_message require idempotency_key."""
    return os.environ.get("PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def write_blocked_without_idempotency(arguments: Dict[str, Any], *, operation: str) -> Optional[str]:
    """Return JSON error body if write must be blocked; else None."""
    if allow_writes_without_idempotency_key():
        return None
    if str(arguments.get("idempotency_key") or "").strip():
        return None
    return tool_error_json(
        ERROR_IDEMPOTENCY_REQUIRED,
        (
            f"idempotency_key is required for {operation}. "
            "Use a stable unique value per logical write (e.g. job-step UUID). "
            "For local development only, set PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY=true."
        ),
        operation=operation,
    )


def tool_error_json(code: str, message: str, **extra: Any) -> str:
    payload: Dict[str, Any] = {"error": code, "message": message}
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    return json.dumps(payload, indent=2)


def output_validation_enabled() -> bool:
    return os.environ.get("PLATFORM_MCP_VALIDATE_TOOL_OUTPUT", "").strip().lower() in ("1", "true", "yes")


def _validate_channels_shape(data: Dict[str, Any]) -> Optional[str]:
    ch = data.get("channels")
    if not isinstance(ch, list):
        return tool_error_json(
            ERROR_OUTPUT_VALIDATION_FAILED,
            "list_channels response must include a JSON array 'channels'",
            provider="slack",
        )
    return None


def _validate_messages_shape(data: Dict[str, Any]) -> Optional[str]:
    m = data.get("messages")
    if not isinstance(m, list):
        return tool_error_json(
            ERROR_OUTPUT_VALIDATION_FAILED,
            "list_messages response must include a JSON array 'messages'",
            provider="slack",
        )
    return None


def _validate_teams_list_teams(data: Dict[str, Any]) -> Optional[str]:
    t = data.get("teams")
    if not isinstance(t, list):
        return tool_error_json(
            ERROR_OUTPUT_VALIDATION_FAILED,
            "list_joined_teams response must include a JSON array 'teams'",
            provider="graph",
        )
    return None


def _validate_teams_list_channels(data: Dict[str, Any]) -> Optional[str]:
    c = data.get("channels")
    if not isinstance(c, list):
        return tool_error_json(
            ERROR_OUTPUT_VALIDATION_FAILED,
            "list_channels response must include a JSON array 'channels'",
            provider="graph",
        )
    return None


def maybe_validate_messaging_output(tool: str, action: str, payload_str: str) -> str:
    """If PLATFORM_MCP_VALIDATE_TOOL_OUTPUT is set, validate known success shapes; else pass-through."""
    if not output_validation_enabled():
        return payload_str
    try:
        data = json.loads(payload_str)
    except (json.JSONDecodeError, TypeError):
        return payload_str
    if not isinstance(data, dict):
        return payload_str
    if data.get("error"):
        return payload_str
    err: Optional[str] = None
    if tool == "slack":
        if action == "list_channels":
            err = _validate_channels_shape(data)
        elif action == "list_messages":
            err = _validate_messages_shape(data)
    elif tool == "teams":
        if action == "list_joined_teams":
            err = _validate_teams_list_teams(data)
        elif action == "list_channels":
            err = _validate_teams_list_channels(data)
    return err if err is not None else payload_str
