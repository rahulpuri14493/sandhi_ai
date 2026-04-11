"""Unit tests for BRD-aware platform tool assignment (tool_splitter)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tool_splitter import (
    _build_write_stub,
    _platform_tool_name,
    _tool_catalog_lines,
    suggest_tool_assignments_for_agents,
)


def test_platform_tool_name_sanitizes_and_truncates():
    assert _platform_tool_name(12, "My Tool!") == "platform_12_My_Tool_"
    assert _platform_tool_name(3, "") == "platform_3"


def test_tool_catalog_lines_includes_summary():
    tools = [
        SimpleNamespace(id=1, name="P", tool_type="postgres"),
        SimpleNamespace(id=2, name="V", tool_type=SimpleNamespace(value="vector_db")),
    ]
    text = _tool_catalog_lines(tools)
    assert "id=1" in text and "postgres" in text
    assert "id=2" in text and "vector_db" in text


def test_build_write_stub_sql_and_object_surfaces():
    tools = [
        SimpleNamespace(id=10, name="PG", tool_type="postgres"),
        SimpleNamespace(id=11, name="BQ", tool_type="bigquery"),
        SimpleNamespace(id=12, name="S3", tool_type="s3"),
        SimpleNamespace(id=13, name="AZ", tool_type="azure_blob"),
        SimpleNamespace(id=14, name="G", tool_type="gcs"),
        SimpleNamespace(id=15, name="FS", tool_type="filesystem"),
        SimpleNamespace(id=16, name="Slack", tool_type="slack"),
    ]
    stub = _build_write_stub(tools)
    assert stub["version"] == "1.0"
    assert stub["write_policy"]["min_successful_targets"] == 1
    wt = stub["write_targets"]
    names = {x["tool_name"] for x in wt}
    assert "platform_10_PG" in names
    assert "platform_11_BQ" in names
    bq = next(x for x in wt if x["tool_name"] == "platform_11_BQ")
    assert bq["target"].get("database") == "<your_database>"
    assert "platform_12_S3" in names
    assert "platform_13_AZ" in names
    assert "platform_14_G" in names
    assert "platform_15_FS" in names
    assert "platform_16_Slack" not in names


@pytest.mark.asyncio
async def test_suggest_tools_empty_platform_tools_returns_fallback():
    agent = SimpleNamespace(name="A", description="d")
    out = await suggest_tool_assignments_for_agents(
        job_title="t",
        job_description="d",
        documents_content=None,
        conversation_data=None,
        agents=[agent],
        platform_tools=[],
    )
    assert out["fallback_used"] is True
    assert out["step_suggestions"] == []
    assert out["output_contract_stub"] is None


@pytest.mark.asyncio
async def test_suggest_tools_no_splitter_url_uses_fallback_partition():
    tools = [
        SimpleNamespace(id=1, name="Pine", tool_type="pinecone"),
        SimpleNamespace(id=2, name="PG", tool_type="postgres"),
    ]
    agents = [
        SimpleNamespace(name="A0", description="x"),
        SimpleNamespace(name="A1", description="y"),
    ]
    out = await suggest_tool_assignments_for_agents(
        job_title="job",
        job_description="desc",
        documents_content=None,
        conversation_data=None,
        agents=agents,
        platform_tools=tools,
    )
    assert out["fallback_used"] is True
    assert len(out["step_suggestions"]) == 2
    assert out["output_contract_stub"] is not None


@pytest.mark.asyncio
async def test_suggest_tools_planner_success_parses_json_array():
    tools = [
        SimpleNamespace(id=7, name="W", tool_type="weaviate"),
    ]
    agents = [SimpleNamespace(name="Only", description="d")]
    raw = '[{"agent_index": 0, "platform_tool_ids": [7], "rationale": "needs search"}]'
    with patch("services.tool_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.tool_splitter.planner_chat_completion",
            new=AsyncMock(return_value=raw),
        ):
            out = await suggest_tool_assignments_for_agents(
                job_title="t",
                job_description="d",
                documents_content=[{"id": "1", "name": "BRD", "content": "short"}],
                conversation_data=None,
                agents=agents,
                platform_tools=tools,
            )
    assert out["fallback_used"] is False
    assert out["step_suggestions"][0]["platform_tool_ids"] == [7]
    assert "rationale" in out["step_suggestions"][0]
    assert out["output_contract_stub"] is not None


@pytest.mark.asyncio
async def test_suggest_tools_fills_llm_audit_on_planner():
    tools = [SimpleNamespace(id=7, name="W", tool_type="weaviate")]
    agents = [SimpleNamespace(name="Only", description="d")]
    raw = '[{"agent_index": 0, "platform_tool_ids": [7], "rationale": "x"}]'
    audit: dict = {}
    with patch("services.tool_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.tool_splitter.planner_chat_completion",
            new=AsyncMock(return_value=raw),
        ):
            await suggest_tool_assignments_for_agents(
                job_title="t",
                job_description="d",
                documents_content=None,
                conversation_data=None,
                agents=agents,
                platform_tools=tools,
                llm_audit=audit,
            )
    assert audit.get("raw_llm_response") == raw
    assert audit.get("source") == "planner"


@pytest.mark.asyncio
async def test_suggest_tools_planner_uses_agent_planner_temperature(monkeypatch):
    """Codex: planner path must use AGENT_PLANNER_TEMPERATURE, not splitter_agent.temperature."""
    from core.config import settings

    tools = [SimpleNamespace(id=7, name="W", tool_type="weaviate")]
    agents = [SimpleNamespace(name="Only", description="d")]
    monkeypatch.setattr(settings, "AGENT_PLANNER_TEMPERATURE", 0.22, raising=False)
    raw = json.dumps(
        [{"agent_index": 0, "platform_tool_ids": [7], "rationale": "search"}]
    )
    mock_planner = AsyncMock(return_value=raw)
    with patch("services.tool_splitter.is_agent_planner_configured", return_value=True):
        with patch("services.tool_splitter.planner_chat_completion", mock_planner):
            await suggest_tool_assignments_for_agents(
                job_title="t",
                job_description="d",
                documents_content=[{"id": "1", "name": "BRD", "content": "x"}],
                conversation_data=None,
                agents=agents,
                platform_tools=tools,
            )
    mock_planner.assert_called_once()
    _args, kwargs = mock_planner.call_args
    assert kwargs.get("temperature") == 0.22


@pytest.mark.asyncio
async def test_suggest_tools_skips_planner_when_all_steps_tool_visibility_none():
    tools = [SimpleNamespace(id=1, name="P", tool_type="postgres")]
    agents = [SimpleNamespace(name="A0", description="x"), SimpleNamespace(name="A1", description="y")]
    audit: dict = {}
    with patch("services.tool_splitter.is_agent_planner_configured", return_value=True):
        mock_planner = AsyncMock(return_value="[]")
        with patch("services.tool_splitter.planner_chat_completion", mock_planner):
            out = await suggest_tool_assignments_for_agents(
                job_title="t",
                job_description="d",
                documents_content=None,
                conversation_data=None,
                agents=agents,
                platform_tools=tools,
                llm_audit=audit,
                step_tool_visibility=["none", "none"],
                job_tool_visibility="full",
            )
    mock_planner.assert_not_called()
    assert len(out["step_suggestions"]) == 2
    assert all(len(x["platform_tool_ids"]) == 0 for x in out["step_suggestions"])
    assert out["output_contract_stub"] is None
    assert audit.get("source") == "skipped_tool_visibility_none"
    assert audit.get("persist_tool_suggestion_without_llm") is True


@pytest.mark.asyncio
async def test_suggest_tools_fallback_masks_step_with_tool_visibility_none():
    tools = [
        SimpleNamespace(id=1, name="Pine", tool_type="pinecone"),
        SimpleNamespace(id=2, name="PG", tool_type="postgres"),
    ]
    agents = [
        SimpleNamespace(name="A0", description="x"),
        SimpleNamespace(name="A1", description="y"),
    ]
    with patch("services.tool_splitter.is_agent_planner_configured", return_value=False):
        out = await suggest_tool_assignments_for_agents(
            job_title="job",
            job_description="desc",
            documents_content=None,
            conversation_data=None,
            agents=agents,
            platform_tools=tools,
            step_tool_visibility=["none", "full"],
            job_tool_visibility="full",
        )
    by_idx = {r["agent_index"]: r for r in out["step_suggestions"]}
    assert by_idx[0]["platform_tool_ids"] == []
    assert "none" in (by_idx[0].get("rationale") or "").lower()
    assert len(by_idx[1]["platform_tool_ids"]) > 0


@pytest.mark.asyncio
async def test_suggest_tools_job_level_none_without_per_step_override():
    tools = [SimpleNamespace(id=1, name="P", tool_type="postgres")]
    agents = [SimpleNamespace(name="Only", description="d")]
    with patch("services.tool_splitter.is_agent_planner_configured", return_value=True):
        mock_planner = AsyncMock()
        with patch("services.tool_splitter.planner_chat_completion", mock_planner):
            out = await suggest_tool_assignments_for_agents(
                job_title="t",
                job_description="d",
                documents_content=None,
                conversation_data=None,
                agents=agents,
                platform_tools=tools,
                step_tool_visibility=None,
                job_tool_visibility="none",
            )
    mock_planner.assert_not_called()
    assert out["step_suggestions"][0]["platform_tool_ids"] == []
