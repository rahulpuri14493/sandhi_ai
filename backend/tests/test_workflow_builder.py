"""Unit tests for WorkflowBuilder service."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from models.job import Job, WorkflowStep, JobStatus
from models.agent import Agent
from services.workflow_builder import WorkflowBuilder


async def _mock_split_job_for_agents(*args, **kwargs):
    """Return fixed task list for tests."""
    return [
        {"agent_index": 0, "task": "Task 1"},
        {"agent_index": 1, "task": "Task 2"},
    ]


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def sample_job():
    """Create sample job with conversation and files."""
    job = MagicMock(spec=Job)
    job.id = 1
    job.business_id = 1
    job.title = "Test Job"
    job.description = "Test description"
    job.conversation = json.dumps([{"question": "Q1", "answer": "A1"}])
    job.files = json.dumps(
        [{"path": "/tmp/test.txt", "name": "test.txt", "type": "text/plain"}]
    )
    return job


@pytest.fixture
def sample_agents():
    """Create sample agents."""
    agents = []
    for i in range(2):
        agent = MagicMock(spec=Agent)
        agent.id = i + 1
        agent.name = f"Agent {i + 1}"
        agent.description = f"Description {i + 1}"
        agent.price_per_task = 5.0
        agent.price_per_communication = 0.5
        agents.append(agent)
    return agents


def test_auto_split_workflow_job_not_found(mock_db):
    """auto_split_workflow raises ValueError when job not found."""
    mock_db.query.return_value.filter.return_value.first.return_value = None
    builder = WorkflowBuilder(mock_db)
    with pytest.raises(ValueError, match="Job not found"):
        builder.auto_split_workflow(999, [1, 2])


def test_auto_split_workflow_agents_not_found(mock_db, sample_job):
    """auto_split_workflow raises ValueError when some agents not found."""
    mock_db.query.return_value.filter.return_value.first.return_value = sample_job
    mock_db.query.return_value.filter.return_value.all.return_value = []  # No agents
    builder = WorkflowBuilder(mock_db)
    with pytest.raises(ValueError, match="Some agents not found"):
        builder.auto_split_workflow(1, [1, 2])


def test_workflow_builder_initialization(mock_db):
    """WorkflowBuilder initializes with PaymentProcessor."""
    builder = WorkflowBuilder(mock_db)
    assert builder.db == mock_db
    assert hasattr(builder, "payment_processor")
    assert builder.payment_processor.db == mock_db


# ---------- Positive test cases (valid workflow creation) ----------


def test_positive_sequential_workflow_sets_depends_on_previous(
    mock_db, sample_job, sample_agents
):
    """When workflow_mode is sequential, step 2+ should have depends_on_previous=True; step 1 False."""
    mock_db.query.return_value.filter.return_value.first.side_effect = [
        sample_job
    ] + sample_agents
    mock_db.query.return_value.filter.return_value.all.side_effect = [
        sample_agents,
        [],  # existing step ids
        [],  # earnings
        [],  # comm ids
        [],  # agent communications to delete
    ]
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    mock_db.commit = MagicMock()
    with patch.object(
        WorkflowBuilder, "_get_workflow_collaboration_hint", return_value="sequential"
    ):
        with patch(
            "services.workflow_builder.split_job_for_agents",
            new_callable=AsyncMock,
            side_effect=_mock_split_job_for_agents,
        ):
            builder = WorkflowBuilder(mock_db)
            with patch.object(
                builder.payment_processor,
                "calculate_job_cost",
                return_value=MagicMock(),
            ):
                builder.auto_split_workflow(1, [1, 2], workflow_mode="sequential")
    # Inspect added WorkflowStep instances: step 1 depends_on_previous=False, step 2 True
    steps_added = [
        c[0][0]
        for c in mock_db.add.call_args_list
        if c[0] and isinstance(c[0][0], WorkflowStep)
    ]
    step1 = next((s for s in steps_added if s.step_order == 1), None)
    step2 = next((s for s in steps_added if s.step_order == 2), None)
    assert step1 is not None and step1.depends_on_previous is False
    assert step2 is not None and step2.depends_on_previous is True


def test_positive_independent_workflow_sets_depends_on_previous_false_for_step2(
    mock_db, sample_job, sample_agents
):
    """When workflow_mode is independent, step 2+ should have depends_on_previous=False."""
    mock_db.query.return_value.filter.return_value.first.side_effect = [
        sample_job
    ] + sample_agents
    mock_db.query.return_value.filter.return_value.all.side_effect = [
        sample_agents,
        [],
        [],
        [],
        [],
    ]
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    mock_db.commit = MagicMock()
    with patch.object(
        WorkflowBuilder, "_get_workflow_collaboration_hint", return_value="async_a2a"
    ):
        with patch(
            "services.workflow_builder.split_job_for_agents",
            new_callable=AsyncMock,
            side_effect=_mock_split_job_for_agents,
        ):
            builder = WorkflowBuilder(mock_db)
            with patch.object(
                builder.payment_processor,
                "calculate_job_cost",
                return_value=MagicMock(),
            ):
                builder.auto_split_workflow(1, [1, 2], workflow_mode="independent")
    steps_added = [
        c[0][0]
        for c in mock_db.add.call_args_list
        if c[0] and isinstance(c[0][0], WorkflowStep)
    ]
    step2 = next((s for s in steps_added if s.step_order == 2), None)
    assert step2 is not None and step2.depends_on_previous is False


def test_auto_split_workflow_persists_job_tool_visibility(
    mock_db, sample_job, sample_agents
):
    """When tool_visibility is passed, job.tool_visibility is set and steps get it."""
    sample_job.allowed_platform_tool_ids = None
    sample_job.allowed_connection_ids = None
    mock_db.query.return_value.filter.return_value.first.side_effect = [
        sample_job
    ] + sample_agents
    mock_db.query.return_value.filter.return_value.all.side_effect = [
        sample_agents,
        [],
        [],
        [],
        [],
    ]
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    mock_db.commit = MagicMock()
    with patch.object(
        WorkflowBuilder, "_get_workflow_collaboration_hint", return_value="sequential"
    ):
        with patch(
            "services.workflow_builder.split_job_for_agents",
            new_callable=AsyncMock,
            side_effect=_mock_split_job_for_agents,
        ):
            builder = WorkflowBuilder(mock_db)
            with patch.object(
                builder.payment_processor,
                "calculate_job_cost",
                return_value=MagicMock(),
            ):
                builder.auto_split_workflow(
                    1, [1, 2], workflow_mode="sequential", tool_visibility="names_only"
                )
    assert getattr(sample_job, "tool_visibility", None) == "names_only"
    steps_added = [
        c[0][0]
        for c in mock_db.add.call_args_list
        if c[0] and isinstance(c[0][0], WorkflowStep)
    ]
    for step in steps_added:
        assert getattr(step, "tool_visibility", None) == "names_only"


def test_auto_split_workflow_step_tool_visibility_override(
    mock_db, sample_job, sample_agents
):
    """step_tools with tool_visibility overrides job-level for that step."""
    sample_job.tool_visibility = "full"
    mock_db.query.return_value.filter.return_value.first.side_effect = [
        sample_job
    ] + sample_agents
    mock_db.query.return_value.filter.return_value.all.side_effect = [
        sample_agents,
        [],
        [],
        [],
        [],
    ]
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    mock_db.commit = MagicMock()
    with patch.object(
        WorkflowBuilder, "_get_workflow_collaboration_hint", return_value="sequential"
    ):
        with patch(
            "services.workflow_builder.split_job_for_agents",
            new_callable=AsyncMock,
            side_effect=_mock_split_job_for_agents,
        ):
            builder = WorkflowBuilder(mock_db)
            with patch.object(
                builder.payment_processor,
                "calculate_job_cost",
                return_value=MagicMock(),
            ):
                builder.auto_split_workflow(
                    1,
                    [1, 2],
                    workflow_mode="sequential",
                    tool_visibility="full",
                    step_tools=[
                        {"agent_index": 0, "tool_visibility": "none"},
                        {"agent_index": 1, "tool_visibility": "names_only"},
                    ],
                )
    steps_added = [
        c[0][0]
        for c in mock_db.add.call_args_list
        if c[0] and isinstance(c[0][0], WorkflowStep)
    ]
    step1 = next((s for s in steps_added if s.step_order == 1), None)
    step2 = next((s for s in steps_added if s.step_order == 2), None)
    assert step1 is not None and getattr(step1, "tool_visibility", None) == "none"
    assert step2 is not None and getattr(step2, "tool_visibility", None) == "names_only"


# ---------- Negative test cases (invalid inputs, expect ValueError) ----------
# See also test_auto_split_workflow_job_not_found and test_auto_split_workflow_agents_not_found above.
