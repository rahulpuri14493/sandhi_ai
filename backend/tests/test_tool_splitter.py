"""Unit tests for BRD-aware platform tool assignment (tool_splitter)."""

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
        splitter_agent=agent,
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
    splitter = SimpleNamespace(
        name="S",
        description="d",
        api_endpoint="",
        api_key=None,
        llm_model=None,
        temperature=None,
    )
    out = await suggest_tool_assignments_for_agents(
        job_title="job",
        job_description="desc",
        documents_content=None,
        conversation_data=None,
        agents=agents,
        platform_tools=tools,
        splitter_agent=splitter,
    )
    assert out["fallback_used"] is True
    assert len(out["step_suggestions"]) == 2
    assert out["output_contract_stub"] is not None


@pytest.mark.asyncio
async def test_suggest_tools_llm_success_parses_json_array():
    tools = [
        SimpleNamespace(id=7, name="W", tool_type="weaviate"),
    ]
    agents = [SimpleNamespace(name="Only", description="d")]
    splitter = SimpleNamespace(
        name="Split",
        description="d",
        api_endpoint="https://llm.example/v1/chat/completions",
        api_key="k",
        llm_model="gpt-4o-mini",
        temperature=0.2,
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '[{"agent_index": 0, "platform_tool_ids": [7], "rationale": "needs search"}]'
                }
            }
        ]
    }
    with patch(
        "services.tool_splitter.post_openai_compatible_raw",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        out = await suggest_tool_assignments_for_agents(
            job_title="t",
            job_description="d",
            documents_content=[{"id": "1", "name": "BRD", "content": "short"}],
            conversation_data=None,
            agents=agents,
            platform_tools=tools,
            splitter_agent=splitter,
        )
    assert out["fallback_used"] is False
    assert out["step_suggestions"][0]["platform_tool_ids"] == [7]
    assert "rationale" in out["step_suggestions"][0]
    assert out["output_contract_stub"] is not None
