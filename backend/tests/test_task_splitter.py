"""Unit tests for task_splitter service (multi-agent workflow)."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

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

    with patch("services.task_splitter.httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_resp
        )
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


def test_split_success_parses_json_from_api(multi_agents):
    """When API returns valid JSON, uses parsed tasks."""
    splitter = multi_agents[0]
    splitter.api_endpoint = "https://api.example.com/chat"
    splitter.api_key = "sk-xxx"

    api_response = [
        {"agent_index": 0, "task": "Task A for agent 1"},
        {"agent_index": 1, "task": "Task B for agent 2"},
        {"agent_index": 2, "task": "Task C for agent 3"},
    ]

    with patch("services.task_splitter.httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(api_response)}}]
        }
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_resp
        )
        result = asyncio.run(split_job_for_agents(
            job_title="API Job",
            job_description="Use API",
            documents_content=[],
            conversation_data=None,
            agents=multi_agents,
            splitter_agent=splitter,
        ))
    assert len(result) == 3
    assert result[0]["task"] == "Task A for agent 1"
    assert result[1]["task"] == "Task B for agent 2"
    assert result[2]["task"] == "Task C for agent 3"


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
    with patch("services.task_splitter.httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": raw_content}}]
        }
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_resp
        )
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
