"""
Canonical shape for job step artifacts used by all platform write targets (Postgres, S3, Snowflake, …).

**Tabular contract**
  ``{"records": [ {<column>: <value>, …}, … ]}``

**Legacy / adapter shapes** (normalized to the same ``records`` list before persistence or load):
  - ``{"content": "<json or markdown-fenced json>"}`` — common for OpenAI/A2A adapter final message
  - One JSONL line that is the whole object above
  - One JSON file (output_artifact_format=json) with ``records`` at top level or only ``content``

Two call sites use slightly different rules:
  - **Agent output** (before writing the artifact file): unwrap ``content`` whenever present and parseable,
    so handoffs match what models return.
  - **Parsed artifact lines** (platform MCP reading JSONL): only unwrap ``{"content": "..."}`` when
    ``content`` is the **sole** key, matching persisted one-line adapter blobs and avoiding accidental
    parsing of mixed payloads.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def strip_markdown_json_fence(text: str) -> str:
    """Remove leading/trailing markdown code fences (e.g. json, plaintext)."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _try_json_loads(s: str) -> Optional[Any]:
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _rows_from_parsed_dict(parsed: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    rec = parsed.get("records")
    if isinstance(rec, list) and rec and all(isinstance(x, dict) for x in rec):
        return rec
    return None


def _extract_rows_from_content_string(content: str) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(content, str) or not content.strip():
        return None
    s = strip_markdown_json_fence(content.strip())
    if not (s.startswith("{") or s.startswith("[")):
        return None
    parsed = _try_json_loads(s)
    if isinstance(parsed, dict):
        rows = _rows_from_parsed_dict(parsed)
        if rows is not None:
            return rows
    return None


def extract_record_rows_from_agent_output(output_data: Any) -> Optional[List[Dict[str, Any]]]:
    """
    If ``output_data`` can be interpreted as tabular rows (agent / executor path), return those rows.

    Uses **loose** ``content`` handling: any dict with a parseable ``content`` string may yield ``records``.
    """
    if output_data is None:
        return None
    if isinstance(output_data, list):
        if all(isinstance(x, dict) for x in output_data):
            return output_data  # type: ignore[return-value]
        return None
    if not isinstance(output_data, dict):
        return None
    direct = _rows_from_parsed_dict(output_data)
    if direct is not None:
        return direct
    c = output_data.get("content")
    if isinstance(c, str):
        return _extract_rows_from_content_string(c)
    return None


def normalize_step_output_for_artifact_file(output_data: Any) -> Any:
    """
    Normalize agent/step output before writing the artifact file (S3/local).

    When the payload is JSON with a top-level ``records`` array (including inside ``content``), returns
    that structure so JSONL persistence can emit one line per row. Preserves a parsed object from
    ``content`` when it includes extra keys beside ``records``.
    """
    if isinstance(output_data, dict) and isinstance(output_data.get("records"), list):
        return output_data
    if isinstance(output_data, dict):
        c = output_data.get("content")
        if isinstance(c, str) and c.strip():
            s = strip_markdown_json_fence(c.strip())
            if s.startswith("{") or s.startswith("["):
                parsed = _try_json_loads(s)
                if isinstance(parsed, dict) and isinstance(parsed.get("records"), list):
                    return parsed
    rows = extract_record_rows_from_agent_output(output_data)
    if rows is not None:
        return {"records": rows}
    return output_data


def normalize_parsed_artifact_lines(parsed_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    After reading JSON/JSONL bytes into a list of dicts (one per line or whole file),
    return the flat list of **row** dicts for platform writes.

    For a **single** parsed line, expands ``{"records": [ ... ]}`` and unwraps fenced JSON in
    ``content`` only when ``content`` is the only key (persisted adapter shape).
    Multiple lines are returned unchanged.
    """
    if not parsed_lines:
        return []
    if len(parsed_lines) > 1:
        return parsed_lines
    single = parsed_lines[0]
    if not isinstance(single, dict):
        return []
    inner = single.get("records")
    if isinstance(inner, list) and inner and all(isinstance(x, dict) for x in inner):
        return inner
    if list(single.keys()) == ["content"]:
        rows = _extract_rows_from_content_string(single["content"])
        if rows is not None:
            return rows
    return [single]


# Backward-compatible name for tests and callers
normalize_agent_output_for_artifact = normalize_step_output_for_artifact_file
