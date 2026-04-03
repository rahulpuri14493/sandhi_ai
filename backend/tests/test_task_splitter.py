"""Unit tests for task_splitter service (multi-agent workflow)."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.agent import Agent
from services.task_splitter import (
    split_job_for_agents,
    _fallback_tasks,
    _build_full_task_context,
)


@pytest.fixture
def single_agent():
    """Single agent for single-agent job tests."""
    a = MagicMock(spec=Agent)
    a.id = 1
    a.name = "Solo Agent"
    a.description = "Does everything"
    return a


@pytest.fixture
def multi_agents():
    """Multiple agents for multi-agent tests."""
    agents = []
    for i in range(3):
        a = MagicMock(spec=Agent)
        a.id = i + 1
        a.name = f"Agent {i + 1}"
        a.description = f"Expert {i + 1}"
        agents.append(a)
    return agents


def test_split_single_agent_returns_full_context(single_agent):
    """Single agent gets full task context, no API call."""
    result = asyncio.run(split_job_for_agents(
        job_title="Test Job",
        job_description="Do something",
        documents_content=[{"name": "doc.txt", "content": "Hello"}],
        conversation_data=None,
        agents=[single_agent],
        splitter_agent=single_agent,
    ))
    assert len(result) == 1
    assert result[0]["agent_index"] == 0
    assert "Test Job" in result[0]["task"]
    assert "Do something" in result[0]["task"]
    assert "Hello" in result[0]["task"]


def test_split_fallback_when_no_api_endpoint(multi_agents):
    """When splitter has no api_endpoint, uses fallback tasks."""
    splitter = multi_agents[0]
    splitter.api_endpoint = None
    result = asyncio.run(split_job_for_agents(
        job_title="Multi Job",
        job_description="Split this",
        documents_content=[],
        conversation_data=None,
        agents=multi_agents,
        splitter_agent=splitter,
    ))
    assert len(result) == 3
    for i, r in enumerate(result):
        assert r["agent_index"] == i
        assert "task" in r
        assert "Multi Job" in r["task"] or "Agent" in r["task"]


def test_split_fallback_when_api_returns_error(multi_agents):
    """When API returns 4xx/5xx, uses fallback."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = None

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        result = asyncio.run(split_job_for_agents(
            job_title="Fail Job",
            job_description="Will fail",
            documents_content=[],
            conversation_data=None,
            agents=multi_agents,
            splitter_agent=splitter,
        ))
    assert len(result) == 3
    for i, r in enumerate(result):
        assert r["agent_index"] == i


@pytest.mark.asyncio
async def test_split_populates_llm_audit_when_planner_used(multi_agents):
    """With platform planner configured, audit gets raw text and source=planner."""
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    api_response = [
        {"agent_index": 0, "task": "P0", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
    ]
    raw_json = json.dumps(api_response)
    audit: dict = {}
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(return_value=raw_json),
        ):
            out = await split_job_for_agents(
                job_title="P",
                job_description="d",
                documents_content=[
                    {"id": "BRD1", "name": "a", "content": "a"},
                    {"id": "BRD2", "name": "b", "content": "b"},
                    {"id": "BRD3", "name": "c", "content": "c"},
                ],
                conversation_data=None,
                agents=multi_agents,
                splitter_agent=splitter,
                llm_audit=audit,
            )
    assert audit.get("raw_llm_response") == raw_json
    assert audit.get("source") == "planner"
    assert len(out) == 3
    assert out[0]["task"] == "P0"


def test_split_populates_llm_audit_when_api_succeeds(multi_agents):
    """Optional llm_audit dict receives raw model text and source."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = "sk-xxx"
    api_response = [
        {"agent_index": 0, "task": "Task A", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "Task B", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "Task C", "assigned_document_ids": ["BRD3"]},
    ]
    raw_json = json.dumps(api_response)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": raw_json}}]}
    audit: dict = {}
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        asyncio.run(
            split_job_for_agents(
                job_title="API Job",
                job_description="Use API",
                documents_content=[
                    {"id": "BRD1", "name": "a.docx", "content": "a"},
                    {"id": "BRD2", "name": "b.docx", "content": "b"},
                    {"id": "BRD3", "name": "c.docx", "content": "c"},
                ],
                conversation_data=None,
                agents=multi_agents,
                splitter_agent=splitter,
                llm_audit=audit,
            )
        )
    assert audit.get("raw_llm_response") == raw_json
    assert audit.get("source") == "agent_endpoint"


def test_split_success_parses_json_from_api(multi_agents):
    """When API returns valid JSON, uses parsed tasks."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = "sk-xxx"

    api_response = [
        {"agent_index": 0, "task": "Task A for agent 1", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "Task B for agent 2", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "Task C for agent 3", "assigned_document_ids": ["BRD3"]},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(api_response)}}]
    }
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        result = asyncio.run(split_job_for_agents(
            job_title="API Job",
            job_description="Use API",
            documents_content=[
                {"id": "BRD1", "name": "a.docx", "content": "a"},
                {"id": "BRD2", "name": "b.docx", "content": "b"},
                {"id": "BRD3", "name": "c.docx", "content": "c"},
            ],
            conversation_data=None,
            agents=multi_agents,
            splitter_agent=splitter,
        ))
    assert len(result) == 3
    assert result[0]["task"] == "Task A for agent 1"
    assert result[1]["task"] == "Task B for agent 2"
    assert result[2]["task"] == "Task C for agent 3"
    assert result[0]["assigned_document_ids"] == ["BRD1"]
    assert result[1]["assigned_document_ids"] == ["BRD2"]
    assert result[2]["assigned_document_ids"] == ["BRD3"]


def test_split_strips_markdown_code_blocks(multi_agents):
    """API response with ```json ... ``` is parsed correctly."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = None

    api_response = [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
        {"agent_index": 2, "task": "Task 3"},
    ]
    raw_content = "```json\n" + json.dumps(api_response) + "\n```"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": raw_content}}]
    }
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        result = asyncio.run(split_job_for_agents(
            job_title="Markdown Job",
            job_description="",
            documents_content=[],
            conversation_data=None,
            agents=multi_agents,
            splitter_agent=splitter,
        ))
    assert len(result) == 3
    assert result[0]["task"] == "Task 1"
    assert result[1]["task"] == "Task 2"
    assert result[2]["task"] == "Task 3"


def test_split_enforces_explicit_job_description_document_mapping(multi_agents):
    """Explicit mapping in job description should assign BRDs to matching agents."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = None
    api_response = [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
        {"agent_index": 2, "task": "Task 3"},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(api_response)}}]
    }
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        result = asyncio.run(split_job_for_agents(
            job_title="Mapping Job",
            job_description="BRD1 addition handled by Agent1. BRD2 subtraction handled by Agent2.",
            documents_content=[
                {"id": "BRD1", "name": "addition.docx", "content": "add"},
                {"id": "BRD2", "name": "subtraction.pdf", "content": "sub"},
                {"id": "BRD3", "name": "other.txt", "content": "other"},
            ],
            conversation_data=None,
            agents=multi_agents,
            splitter_agent=splitter,
        ))
    assert result[0]["assigned_document_ids"] == ["BRD1"]
    assert result[1]["assigned_document_ids"] == ["BRD2"]


def test_split_explicit_mapping_handles_filename_extensions_without_breaking(multi_agents):
    """Doc names with extensions (e.g., .docx) should still map correctly."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = None
    api_response = [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
        {"agent_index": 2, "task": "Task 3"},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(api_response)}}]
    }
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        result = asyncio.run(split_job_for_agents(
            job_title="Mapping With Extensions",
            job_description="anova_test.docx handled by Agent1 and chi_square_test.pdf handled by Agent2",
            documents_content=[
                {"id": "BRD1", "name": "anova_test.docx", "content": "add"},
                {"id": "BRD2", "name": "chi_square_test.pdf", "content": "sub"},
                {"id": "BRD3", "name": "other.txt", "content": "other"},
            ],
            conversation_data=None,
            agents=multi_agents,
            splitter_agent=splitter,
        ))
    assert result[0]["assigned_document_ids"] == ["BRD1"]
    assert result[1]["assigned_document_ids"] == ["BRD2"]


def test_split_explicit_mapping_uses_bounded_token_matching():
    """BRD1/agent1 should not collide with BRD10/agent10."""
    agents = []
    for i in range(10):
        a = MagicMock(spec=Agent)
        a.id = i + 1
        a.name = f"Agent {i + 1}"
        a.description = f"Expert {i + 1}"
        agents.append(a)

    splitter = agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = None
    api_response = [{"agent_index": i, "task": f"Task {i + 1}"} for i in range(10)]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(api_response)}}]
    }
    with patch("services.task_splitter.post_openai_compatible_raw", new=AsyncMock(return_value=mock_resp)):
        result = asyncio.run(split_job_for_agents(
            job_title="Bounded Matching",
            job_description="BRD10 handled by agent10. BRD1 handled by agent1.",
            documents_content=[
                {"id": "BRD1", "name": "a.txt", "content": "one"},
                {"id": "BRD10", "name": "b.txt", "content": "ten"},
            ],
            conversation_data=None,
            agents=agents,
            splitter_agent=splitter,
        ))

    assert result[0]["assigned_document_ids"] == ["BRD1"]
    assert result[9]["assigned_document_ids"] == ["BRD10"]


def test_fallback_tasks_one_per_agent(multi_agents):
    """_fallback_tasks returns one entry per agent."""
    result = _fallback_tasks(
        multi_agents,
        "Job",
        "Desc",
        [{"name": "x", "content": "y"}],
    )
    assert len(result) == 3
    for i, r in enumerate(result):
        assert r["agent_index"] == i
        assert "Job" in r["task"] or "agent" in r["task"].lower()


def test_build_full_task_context_includes_docs():
    """_build_full_task_context includes document content."""
    result = _build_full_task_context(
        "Title",
        "Desc",
        [{"name": "a.txt", "content": "Doc content here"}],
    )
    assert "Title" in result
    assert "Desc" in result
    assert "Doc content here" in result
