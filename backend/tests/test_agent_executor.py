"""Unit tests for AgentExecutor service."""

import json
from unittest.mock import MagicMock

import pytest
from models.agent import Agent
from services.agent_executor import (
    AgentExecutor,
    _apply_tool_visibility,
    _get_workflow_collaboration_hint_from_job,
)


def _get_executor_format_input(agent: Agent, input_data: dict) -> dict:
    """Helper to call _format_input_for_agent via executor."""
    executor = AgentExecutor(db=MagicMock())
    return executor._format_for_openai(agent, input_data)


def test_format_for_openai_includes_documents():
    """Payload should include document content in messages."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test Agent"
    agent.description = "Test"
    input_data = {
        "job_title": "Add numbers",
        "job_description": "Add 2 and 3",
        "documents": [
            {
                "name": "req.txt",
                "type": "text/plain",
                "content": "Add 2 and 3. Result: 5.",
            }
        ],
        "conversation": [],
    }
    payload = _get_executor_format_input(agent, input_data)
    assert "messages" in payload
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "Add 2 and 3" in content_str
    assert "Result: 5" in content_str


def test_format_for_openai_includes_conversation():
    """Payload should include Q&A conversation in messages."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test Agent"
    input_data = {
        "job_title": "Math",
        "job_description": "",
        "documents": [],
        "conversation": [
            {"type": "question", "question": "What numbers?", "answer": "2 and 3"}
        ],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "What numbers?" in content_str
    assert "2 and 3" in content_str


def test_format_for_openai_rejects_extraction_error_content():
    """Content like '[Error extracting...' should trigger fallback, not be sent as doc."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [
            {
                "name": "x.docx",
                "type": "docx",
                "content": "[DOCX extraction requires python-docx library. File: x.docx]",
            }
        ],
        "conversation": [],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    # Should use fallback instructing to use job title/description, not the raw error
    assert (
        "could not extract" in content_str
        or "Text could not be extracted" in content_str
    )
    assert "JOB TITLE" in content_str or "job title" in content_str.lower()


def test_format_for_openai_accepts_real_content_starting_with_bracket():
    """Real requirement text starting with '[' should be sent, not rejected."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [
            {
                "name": "req.txt",
                "type": "text",
                "content": "[Requirement] Add 2 and 3. The task requires the sum.",
            }
        ],
        "conversation": [],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "[Requirement] Add 2 and 3" in content_str
    assert "requires" in content_str


def test_format_for_openai_includes_assigned_task_multi_agent():
    """Multi-agent workflow: assigned_task appears in system message."""
    agent = MagicMock(spec=Agent)
    agent.name = "Agent 2"
    input_data = {
        "job_title": "Multi Job",
        "job_description": "Split work",
        "documents": [],
        "conversation": [],
        "assigned_task": "Analyze the output from Agent 1 and summarize.",
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "YOUR ASSIGNED TASK" in content_str
    assert "Analyze the output from Agent 1" in content_str


def test_format_for_openai_includes_previous_step_output():
    """Multi-agent workflow: previous_step_output appears in messages."""
    agent = MagicMock(spec=Agent)
    agent.name = "Agent 2"
    input_data = {
        "job_title": "Chain Job",
        "job_description": "",
        "documents": [],
        "conversation": [],
        "assigned_task": "Use previous result",
        "previous_step_output": {"result": "Step 1 complete"},
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "INTER-AGENT COMMUNICATION" in content_str
    assert "Step 1 complete" in content_str


# ---------- Positive test cases (MCP + sequential) ----------


def test_positive_format_includes_available_mcp_tools():
    """Payload should include MCP tools section when available_mcp_tools is present."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test Agent"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [],
        "conversation": [],
        "available_mcp_tools": [
            {
                "name": "platform_1_my_db",
                "description": "PostgreSQL: My DB",
                "source": "platform",
            },
        ],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "AVAILABLE MCP TOOLS" in content_str
    assert "platform_1_my_db" in content_str
    assert "PostgreSQL" in content_str


def test_positive_format_sequential_workflow_message_when_previous_output():
    """When previous_step_output and multi-step, message mentions sequential workflow."""
    agent = MagicMock(spec=Agent)
    agent.name = "Agent 2"
    input_data = {
        "job_title": "Pipeline",
        "job_description": "",
        "documents": [],
        "conversation": [],
        "step_order": 2,
        "total_steps": 2,
        "previous_step_output": {"content": "First step result"},
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert (
        "INTER-AGENT COMMUNICATION" in content_str or "previous" in content_str.lower()
    )
    assert "First step result" in content_str


def test_positive_format_assigned_task_with_sequential_hint():
    """Multi-agent with assigned_task and previous_step_output gets sequential hint."""
    agent = MagicMock(spec=Agent)
    agent.name = "Agent 2"
    input_data = {
        "job_title": "Multi",
        "job_description": "",
        "documents": [],
        "conversation": [],
        "assigned_task": "Summarize previous output",
        "step_order": 2,
        "total_steps": 2,
        "previous_step_output": {"content": "Data from step 1"},
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "sequential" in content_str.lower() or "previous" in content_str.lower()
    assert "Data from step 1" in content_str


# ---------- Negative test cases (missing/empty optional, no crash) ----------


def test_negative_format_handles_empty_documents():
    """Payload builds without crashing when documents is empty list."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [],
        "conversation": [],
    }
    payload = _get_executor_format_input(agent, input_data)
    assert "messages" in payload
    assert len(payload["messages"]) >= 1


def test_negative_format_handles_missing_optional_keys():
    """Payload builds when optional keys (conversation, documents, assigned_task) are absent."""
    agent = MagicMock(spec=Agent)
    agent.name = "Minimal"
    input_data = {
        "job_title": "Minimal Job",
        "job_description": "",
    }
    payload = _get_executor_format_input(agent, input_data)
    assert "messages" in payload
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "Minimal Job" in content_str


def test_negative_format_handles_empty_available_mcp_tools():
    """When available_mcp_tools is empty list, no MCP section but payload is valid."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [],
        "conversation": [],
        "available_mcp_tools": [],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "messages" in payload
    # Should not crash; MCP section may be omitted when empty
    assert "Job" in content_str


# ---------- Tool visibility and peer A2A ----------


def test_apply_tool_visibility_none_returns_empty():
    """tool_visibility 'none' returns empty list."""
    tools = [{"name": "t1", "description": "d1", "schema_metadata": "s1"}]
    assert _apply_tool_visibility(tools, "none") == []
    assert _apply_tool_visibility([], "full") == []


def test_apply_tool_visibility_names_only_strips_schema():
    """tool_visibility 'names_only' returns name/description only, no schema or business_description."""
    tools = [
        {
            "name": "db1",
            "description": "PostgreSQL DB",
            "schema_metadata": "{}",
            "business_description": "Sales DB",
        },
    ]
    out = _apply_tool_visibility(tools, "names_only")
    assert len(out) == 1
    assert out[0]["name"] == "db1"
    assert "PostgreSQL" in out[0]["description"]
    assert "schema_metadata" not in out[0]
    assert "business_description" not in out[0]


def test_apply_tool_visibility_full_returns_unchanged():
    """tool_visibility 'full' returns tools unchanged."""
    tools = [{"name": "t1", "description": "d1", "schema_metadata": "x"}]
    assert _apply_tool_visibility(tools, "full") == tools
    assert _apply_tool_visibility(tools, None) == tools


def test_get_workflow_collaboration_hint_from_job_sequential():
    """Extract workflow_collaboration_hint 'sequential' from job conversation."""
    job = MagicMock()
    job.conversation = json.dumps(
        [
            {"type": "question", "question": "Q1"},
            {"type": "completion", "workflow_collaboration_hint": "sequential"},
        ]
    )
    assert _get_workflow_collaboration_hint_from_job(job) == "sequential"


def test_get_workflow_collaboration_hint_from_job_async_a2a():
    """Extract workflow_collaboration_hint 'async_a2a' from job conversation."""
    job = MagicMock()
    job.conversation = json.dumps(
        [
            {"workflow_collaboration_hint": "async_a2a"},
        ]
    )
    assert _get_workflow_collaboration_hint_from_job(job) == "async_a2a"


def test_get_workflow_collaboration_hint_from_job_empty_returns_none():
    """When conversation is empty or missing hint, returns None."""
    job = MagicMock()
    job.conversation = None
    assert _get_workflow_collaboration_hint_from_job(job) is None
    job.conversation = json.dumps([{"type": "question"}])
    assert _get_workflow_collaboration_hint_from_job(job) is None


def test_format_for_openai_includes_peer_agents_when_present():
    """When peer_agents in input_data, system message includes PEER AGENTS section."""
    agent = MagicMock(spec=Agent)
    agent.name = "Agent 1"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [],
        "conversation": [],
        "peer_agents": [
            {
                "agent_id": 2,
                "name": "Agent 2",
                "a2a_endpoint": "https://a2.example.com",
                "step_order": 2,
            },
        ],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    assert "PEER AGENTS" in content_str
    assert "Agent 2" in content_str
    # Peer line format: " - Name (step N): <endpoint>"; assert step part to confirm endpoint slot is present
    assert "(step 2):" in content_str
