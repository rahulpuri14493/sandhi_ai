import json
import pytest
from pathlib import Path


def _resolve_schema_path() -> Path:
    """Repo checkout has ``docs/`` at root; Docker backend image mounts only ``backend/`` → use ``resources/`` copy."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "docs" / "schemas" / "a2a" / "sandhi_a2a_task.v1.schema.json",
        here.parents[1] / "resources" / "schemas" / "a2a" / "sandhi_a2a_task.v1.schema.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    pytest.skip("sandhi_a2a_task.v1.schema.json not found in docs/ or backend/resources/ for this environment")


def test_sandhi_a2a_task_v1_schema_file_is_valid_json():
    schema = _resolve_schema_path()
    assert schema.is_file()
    data = json.loads(schema.read_text(encoding="utf-8"))
    assert data.get("$schema")
    assert data.get("properties", {}).get("schema_version")
    assert "sandhi.a2a_task.v1" in json.dumps(data)
    req = data.get("required") or []
    assert "next_agent" in req and "assigned_tools" in req