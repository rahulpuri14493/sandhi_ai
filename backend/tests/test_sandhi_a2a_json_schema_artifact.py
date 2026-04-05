"""Sanity check: published JSON Schema for ``sandhi.a2a_task.v1`` is present and parseable."""

import json
from pathlib import Path


def test_sandhi_a2a_task_v1_schema_file_is_valid_json():
    root = Path(__file__).resolve().parents[2]
    schema = root / "docs" / "schemas" / "a2a" / "sandhi_a2a_task.v1.schema.json"
    assert schema.is_file()
    data = json.loads(schema.read_text(encoding="utf-8"))
    assert data.get("$schema")
    assert data.get("properties", {}).get("schema_version")
    assert "sandhi.a2a_task.v1" in json.dumps(data)
    req = data.get("required") or []
    assert "next_agent" in req and "assigned_tools" in req
