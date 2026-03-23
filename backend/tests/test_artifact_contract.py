"""Unit tests for core.artifact_contract (shared executor + MCP normalization)."""
import json

from core.artifact_contract import (
    extract_record_rows_from_agent_output,
    normalize_parsed_artifact_lines,
    normalize_step_output_for_artifact_file,
    strip_markdown_json_fence,
)


def test_strip_markdown_fence():
    raw = "```json\n{\"a\": 1}\n```"
    assert strip_markdown_json_fence(raw) == '{"a": 1}'


def test_normalize_agent_output_from_content_json():
    inner = {"records": [{"x": 1}], "meta": "keep"}
    out = normalize_step_output_for_artifact_file({"content": json.dumps(inner)})
    assert out["records"] == [{"x": 1}]
    assert out.get("meta") == "keep"


def test_normalize_parsed_artifact_strict_content_only():
    rows = normalize_parsed_artifact_lines(
        [{"content": "not-json", "extra": 1}]
    )
    assert len(rows) == 1
    assert rows[0].get("extra") == 1


def test_extract_rows_list_of_dicts():
    r = extract_record_rows_from_agent_output([{"a": 1}])
    assert r == [{"a": 1}]


def test_normalize_parsed_single_line_records_wrapper():
    rows = normalize_parsed_artifact_lines([{"records": [{"k": "v"}]}])
    assert rows == [{"k": "v"}]
