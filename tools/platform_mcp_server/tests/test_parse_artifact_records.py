"""parse_artifact_records: unwrap {\"records\": [...]} like job executor artifact layout."""
import json

import pytest

import execution_common

pytestmark = pytest.mark.unit


def test_json_format_expands_records_wrapper():
    payload = {"records": [{"job_creation_date": "2026-03-22", "job_count": 4}]}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    rows = execution_common.parse_artifact_records(raw, "json")
    assert len(rows) == 1
    assert rows[0].get("job_creation_date") == "2026-03-22"
    assert "records" not in rows[0]


def test_jsonl_single_line_expands_records_wrapper():
    payload = {"records": [{"a": 1}, {"a": 2}]}
    line = json.dumps(payload, ensure_ascii=False)
    raw = (line + "\n").encode("utf-8")
    rows = execution_common.parse_artifact_records(raw, "jsonl")
    assert len(rows) == 2
    assert rows[0]["a"] == 1


def test_jsonl_multiple_lines_unchanged():
    raw = b'{"x":1}\n{"x":2}\n'
    rows = execution_common.parse_artifact_records(raw, "jsonl")
    assert len(rows) == 2


def test_json_plain_object_without_records():
    raw = json.dumps({"job_creation_date": "2026-01-01", "job_count": 3}).encode("utf-8")
    rows = execution_common.parse_artifact_records(raw, "json")
    assert len(rows) == 1
    assert rows[0]["job_creation_date"] == "2026-01-01"


def test_jsonl_unwraps_content_with_json_markdown_fence():
    """Matches MinIO artifact: one line {"content": "```json\\n{...records...}\\n```"}."""
    inner = {
        "records": [
            {"job_creation_date": "2026-03-19T00:00:00Z", "job_count": 3},
            {"job_creation_date": "2026-03-22T00:00:00Z", "job_count": 4},
        ]
    }
    fenced = "```json\n" + json.dumps(inner, indent=2) + "\n```"
    line = json.dumps({"content": fenced}, ensure_ascii=False)
    raw = (line + "\n").encode("utf-8")
    rows = execution_common.parse_artifact_records(raw, "jsonl")
    assert len(rows) == 2
    assert rows[0]["job_creation_date"] == "2026-03-19T00:00:00Z"
    assert rows[1]["job_count"] == 4
