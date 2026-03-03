"""Unit tests for AgentExecutor service."""
import json
from unittest.mock import MagicMock

import pytest
from models.agent import Agent
from services.agent_executor import AgentExecutor


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
            {"name": "req.txt", "type": "text/plain", "content": "Add 2 and 3. Result: 5."}
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
            {"name": "x.docx", "type": "docx", "content": "[DOCX extraction requires python-docx library. File: x.docx]"}
        ],
        "conversation": [],
    }
    payload = _get_executor_format_input(agent, input_data)
    content_str = json.dumps([m.get("content", "") for m in payload["messages"]])
    # Should use fallback instructing to use job title/description, not the raw error
    assert "could not extract" in content_str or "Text could not be extracted" in content_str
    assert "JOB TITLE" in content_str or "job title" in content_str.lower()


def test_format_for_openai_accepts_real_content_starting_with_bracket():
    """Real requirement text starting with '[' should be sent, not rejected."""
    agent = MagicMock(spec=Agent)
    agent.name = "Test"
    input_data = {
        "job_title": "Job",
        "job_description": "",
        "documents": [
            {"name": "req.txt", "type": "text", "content": "[Requirement] Add 2 and 3. The task requires the sum."}
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
