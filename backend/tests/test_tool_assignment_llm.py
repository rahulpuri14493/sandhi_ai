"""Tests for ``services.tool_assignment_llm`` JSON parsing and allowlist filtering."""

import pytest

from services import tool_assignment_llm as tal


def test_parse_tool_names_json_object():
    names = tal._parse_tool_names_json('{"tool_names": ["a", "b"]}')
    assert names == ["a", "b"]


def test_parse_tool_names_json_with_markdown_fence():
    raw = 'Here:\n```json\n{"tool_names": ["x"]}\n```'
    assert tal._parse_tool_names_json(raw) == ["x"]


def test_parse_tool_names_json_invalid_returns_empty():
    assert tal._parse_tool_names_json("not json") == []


@pytest.mark.asyncio
async def test_suggest_tool_names_with_llm_filters_to_allowlist(monkeypatch):
    async def fake_completion(messages, **kwargs):
        return '{"tool_names": ["keep", "unknown", "also"]}'

    monkeypatch.setattr(tal, "planner_chat_completion", fake_completion)
    tools = [
        {"name": "keep", "tool_type": "postgres"},
        {"name": "also", "tool_type": "s3"},
    ]
    out = await tal.suggest_tool_names_with_llm(
        job_title="J",
        assigned_task="T",
        task_type="search",
        tools=tools,
        max_names=10,
    )
    assert out == ["keep", "also"]
