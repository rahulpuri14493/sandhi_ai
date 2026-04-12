"""Unit tests for task_splitter service (multi-agent workflow)."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import settings
from models.agent import Agent
from services.task_splitter import (
    PlannerSplitError,
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
    ))
    assert len(result) == 1
    assert result[0]["agent_index"] == 0
    assert "Test Job" in result[0]["task"]
    assert "Do something" in result[0]["task"]
    assert "Hello" in result[0]["task"]


def test_split_fallback_when_planner_not_configured(multi_agents):
    """Without platform planner, multi-agent split uses heuristic fallback tasks."""
    with patch("services.task_splitter.is_agent_planner_configured", return_value=False):
        result = asyncio.run(split_job_for_agents(
            job_title="Multi Job",
            job_description="Split this",
            documents_content=[],
            conversation_data=None,
            agents=multi_agents,
        ))
    assert len(result) == 3
    for i, r in enumerate(result):
        assert r["agent_index"] == i
        assert "task" in r
        assert "Multi Job" in r["task"] or "Agent" in r["task"]


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
                llm_audit=audit,
            )
    assert audit.get("raw_llm_response") == raw_json
    assert audit.get("source") == "planner"
    assert len(out) == 3
    assert out[0]["task"] == "P0"


@pytest.mark.asyncio
async def test_split_includes_optional_assignment_reason(multi_agents):
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    api_response = [
        {
            "agent_index": 0,
            "task": "P0",
            "assigned_document_ids": ["BRD1"],
            "assignment_reason": "Best for data extraction.",
        },
        {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
    ]
    raw_json = json.dumps(api_response)
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
            )
    assert out[0].get("assignment_reason") == "Best for data extraction."
    assert "assignment_reason" not in out[1]


@pytest.mark.asyncio
async def test_split_planner_uses_agent_planner_temperature_not_splitter_agent(
    monkeypatch, multi_agents
):
    """Codex: planner path must use AGENT_PLANNER_TEMPERATURE, not first agent temperature."""
    from core.config import settings

    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    splitter.temperature = 0.99
    monkeypatch.setattr(settings, "AGENT_PLANNER_TEMPERATURE", 0.11, raising=False)
    raw_json = json.dumps(
        [
            {"agent_index": 0, "task": "P0", "assigned_document_ids": ["BRD1"]},
            {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
            {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
        ]
    )
    mock_planner = AsyncMock(return_value=raw_json)
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch("services.task_splitter.planner_chat_completion", mock_planner):
            await split_job_for_agents(
                job_title="P",
                job_description="d",
                documents_content=[
                    {"id": "BRD1", "name": "a", "content": "a"},
                    {"id": "BRD2", "name": "b", "content": "b"},
                    {"id": "BRD3", "name": "c", "content": "c"},
                ],
                conversation_data=None,
                agents=multi_agents,
            )
    mock_planner.assert_called_once()
    _args, kwargs = mock_planner.call_args
    assert kwargs.get("temperature") == 0.11


def test_split_strips_markdown_code_blocks_planner(multi_agents):
    """Planner response with ```json ... ``` is parsed correctly."""
    api_response = [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
        {"agent_index": 2, "task": "Task 3"},
    ]
    raw_content = "```json\n" + json.dumps(api_response) + "\n```"
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(return_value=raw_content),
        ):
            result = asyncio.run(split_job_for_agents(
                job_title="Markdown Job",
                job_description="",
                documents_content=[],
                conversation_data=None,
                agents=multi_agents,
            ))
    assert len(result) == 3
    assert result[0]["task"] == "Task 1"


def test_split_enforces_explicit_job_description_document_mapping(multi_agents):
    """Explicit mapping in job description should assign BRDs to matching agents."""
    api_response = [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
        {"agent_index": 2, "task": "Task 3"},
    ]
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(return_value=json.dumps(api_response)),
        ):
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
            ))
    assert result[0]["assigned_document_ids"] == ["BRD1"]
    assert result[1]["assigned_document_ids"] == ["BRD2"]


def test_split_explicit_mapping_handles_filename_extensions_without_breaking(multi_agents):
    """Doc names with extensions (e.g., .docx) should still map correctly."""
    api_response = [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
        {"agent_index": 2, "task": "Task 3"},
    ]
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(return_value=json.dumps(api_response)),
        ):
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

    api_response = [{"agent_index": i, "task": f"Task {i + 1}"} for i in range(10)]
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(return_value=json.dumps(api_response)),
        ):
            result = asyncio.run(split_job_for_agents(
                job_title="Bounded Matching",
                job_description="BRD10 handled by agent10. BRD1 handled by agent1.",
                documents_content=[
                    {"id": "BRD1", "name": "a.txt", "content": "one"},
                    {"id": "BRD10", "name": "b.txt", "content": "ten"},
                ],
                conversation_data=None,
                agents=agents,
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


@pytest.mark.asyncio
async def test_split_planner_raises_after_exhausted_attempts(monkeypatch, multi_agents):
    """Configured planner: invalid JSON repeatedly raises PlannerSplitError (no heuristic fallback)."""
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_RETRY_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_JSON_REPAIR", False)
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(return_value="not valid json"),
        ):
            with pytest.raises(PlannerSplitError) as ei:
                await split_job_for_agents(
                    job_title="P",
                    job_description="d",
                    documents_content=[
                        {"id": "BRD1", "name": "a", "content": "a"},
                        {"id": "BRD2", "name": "b", "content": "b"},
                        {"id": "BRD3", "name": "c", "content": "c"},
                    ],
                    conversation_data=None,
                    agents=multi_agents,
                )
    assert ei.value.attempts == 2


@pytest.mark.asyncio
async def test_split_planner_json_repair_succeeds(monkeypatch, multi_agents):
    """After bad JSON, repair completion produces valid split."""
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    fixed = [
        {"agent_index": 0, "task": "P0", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
    ]
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_JSON_REPAIR", True)
    docs = [
        {"id": "BRD1", "name": "a", "content": "a"},
        {"id": "BRD2", "name": "b", "content": "b"},
        {"id": "BRD3", "name": "c", "content": "c"},
    ]
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(side_effect=["not json {", json.dumps(fixed)]),
        ):
            out = await split_job_for_agents(
                job_title="P",
                job_description="d",
                documents_content=docs,
                conversation_data=None,
                agents=multi_agents,
            )
    assert out[0]["task"] == "P0"
    assert out[2]["task"] == "P2"


@pytest.mark.asyncio
async def test_split_planner_calls_reload_between_attempts(monkeypatch, multi_agents):
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_RETRY_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_JSON_REPAIR", False)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_RELOAD_DOCS_BETWEEN_ATTEMPTS", True)
    ok = [
        {"agent_index": 0, "task": "P0", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
    ]
    docs = [
        {"id": "BRD1", "name": "a", "content": "a"},
        {"id": "BRD2", "name": "b", "content": "b"},
        {"id": "BRD3", "name": "c", "content": "c"},
    ]
    reload_mock = AsyncMock(return_value=docs)
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(side_effect=["bad", json.dumps(ok)]),
        ):
            await split_job_for_agents(
                job_title="P",
                job_description="d",
                documents_content=docs,
                conversation_data=None,
                agents=multi_agents,
                reload_documents_content=reload_mock,
            )
    assert reload_mock.await_count >= 1


@pytest.mark.asyncio
async def test_split_planner_http_error_then_success_on_retry(monkeypatch, multi_agents):
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    ok = [
        {"agent_index": 0, "task": "P0", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
    ]
    docs = [
        {"id": "BRD1", "name": "a", "content": "a"},
        {"id": "BRD2", "name": "b", "content": "b"},
        {"id": "BRD3", "name": "c", "content": "c"},
    ]
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_RETRY_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_JSON_REPAIR", False)
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(side_effect=[RuntimeError("timeout"), json.dumps(ok)]),
        ):
            out = await split_job_for_agents(
                job_title="P",
                job_description="d",
                documents_content=docs,
                conversation_data=None,
                agents=multi_agents,
            )
    assert out[0]["task"] == "P0"


@pytest.mark.asyncio
async def test_split_planner_reload_failure_logged_continues(monkeypatch, multi_agents):
    splitter = multi_agents[0]
    splitter.api_endpoint = ""
    ok = [
        {"agent_index": 0, "task": "P0", "assigned_document_ids": ["BRD1"]},
        {"agent_index": 1, "task": "P1", "assigned_document_ids": ["BRD2"]},
        {"agent_index": 2, "task": "P2", "assigned_document_ids": ["BRD3"]},
    ]
    docs = [
        {"id": "BRD1", "name": "a", "content": "a"},
        {"id": "BRD2", "name": "b", "content": "b"},
        {"id": "BRD3", "name": "c", "content": "c"},
    ]
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_RETRY_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_JSON_REPAIR", False)
    monkeypatch.setattr(settings, "AGENT_PLANNER_SPLIT_RELOAD_DOCS_BETWEEN_ATTEMPTS", True)
    bad_reload = AsyncMock(side_effect=OSError("minio down"))
    with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
        with patch(
            "services.task_splitter.planner_chat_completion",
            new=AsyncMock(side_effect=["bad json", json.dumps(ok)]),
        ):
            out = await split_job_for_agents(
                job_title="P",
                job_description="d",
                documents_content=docs,
                conversation_data=None,
                agents=multi_agents,
                reload_documents_content=bad_reload,
            )
    assert out[1]["task"] == "P1"
    assert bad_reload.await_count >= 1


