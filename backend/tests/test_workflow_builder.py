"""Unit tests for WorkflowBuilder service."""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from models.job import Job, WorkflowStep, JobStatus
from models.agent import Agent
from services.workflow_builder import WorkflowBuilder


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
    job.files = json.dumps([{"path": "/tmp/test.txt", "name": "test.txt", "type": "text/plain"}])
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
