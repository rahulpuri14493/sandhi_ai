import json
import pytest
from pathlib import Path

def test_sandhi_a2a_task_v1_schema_file_is_valid_json():
    # Attempt to locate the schema relative to the repo root
    # Level 1: tests, Level 2: backend, Level 3: sandhi_ai (repo root)
    repo_root = Path(__file__).resolve().parent.parent.parent
    schema = repo_root / "docs" / "schemas" / "a2a" / "sandhi_a2a_task.v1.schema.json"

    # If the docs folder isn't mounted (e.g., inside isolated Docker container), skip gracefully
    if not schema.is_file():
        pytest.skip(f"Schema file not available in this environment. Skipped check at: {schema}")

    # Original validation logic
    data = json.loads(schema.read_text(encoding="utf-8"))
    assert data.get("$schema")
    assert data.get("properties", {}).get("schema_version")
    assert "sandhi.a2a_task.v1" in json.dumps(data)
    req = data.get("required") or []
    assert "next_agent" in req and "assigned_tools" in req