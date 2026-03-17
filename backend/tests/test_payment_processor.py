"""Unit tests for PaymentProcessor service."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime

from models.job import Job, WorkflowStep, JobStatus
from models.agent import Agent
from models.user import User
from models.transaction import Transaction
from services.payment_processor import PaymentProcessor


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def sample_job():
    """Create sample job for testing."""
    job = MagicMock(spec=Job)
    job.id = 1
    job.business_id = 1
    job.title = "Test Job"
    job.total_cost = 0.0
    return job


@pytest.fixture
def sample_workflow_steps():
    """Create sample workflow steps with agents."""
    steps = []
    for i in range(2):
        step = MagicMock(spec=WorkflowStep)
        step.id = i + 1
        step.job_id = 1
        step.agent_id = i + 1
        step.step_order = i + 1
        step.cost = 5.0
        step.status = "completed"
        step.input_data = None
        step.output_data = None
        step.started_at = datetime.utcnow()
        step.completed_at = datetime.utcnow()
        steps.append(step)
    return steps


@pytest.fixture
def sample_agents():
    """Create sample agents with pricing."""
    agents = []
    for i in range(2):
        agent = MagicMock(spec=Agent)
        agent.id = i + 1
        agent.name = f"Agent {i + 1}"
        agent.price_per_task = 5.0
        agent.price_per_communication = 0.5
        agents.append(agent)
    return agents


def test_calculate_job_cost_job_not_found(mock_db):
    """calculate_job_cost raises ValueError when job not found."""
    mock_db.query.return_value.filter.return_value.first.return_value = None
    processor = PaymentProcessor(mock_db)
    with pytest.raises(ValueError, match="Job not found"):
        processor.calculate_job_cost(999)


def test_calculate_job_cost_empty_workflow(mock_db, sample_job):
    """calculate_job_cost returns empty preview when no workflow steps."""
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    mock_db.query.return_value.filter.return_value.first.return_value = sample_job
    processor = PaymentProcessor(mock_db)
    result = processor.calculate_job_cost(1)
    assert result.total_cost == 0.0
    assert result.steps == []
    assert result.breakdown["task_costs"] == 0.0
    assert result.breakdown["communication_costs"] == 0.0
    assert result.breakdown["commission"] == 0.0


def test_process_payment_job_not_found(mock_db):
    """process_payment raises ValueError when job not found."""
    mock_db.query.return_value.filter.return_value.first.return_value = None
    processor = PaymentProcessor(mock_db)
    with pytest.raises(ValueError, match="Job not found"):
        processor.process_payment(999)


def test_distribute_earnings_job_not_found(mock_db):
    """distribute_earnings raises ValueError when job not found."""
    mock_db.query.return_value.filter.return_value.first.return_value = None
    processor = PaymentProcessor(mock_db)
    with pytest.raises(ValueError, match="Job not found"):
        processor.distribute_earnings(999)


def test_distribute_earnings_transaction_not_found(mock_db, sample_job):
    """distribute_earnings raises ValueError when transaction not found."""

    def query_side_effect(model):
        q = MagicMock()
        if model == Job:
            q.filter.return_value.first.return_value = sample_job
        else:
            q.filter.return_value.first.return_value = None
        return q

    mock_db.query.side_effect = query_side_effect
    processor = PaymentProcessor(mock_db)
    with pytest.raises(ValueError, match="Transaction not found"):
        processor.distribute_earnings(1)


def test_commission_rate_from_settings():
    """PaymentProcessor uses commission rate from settings."""
    processor = PaymentProcessor(MagicMock())
    assert hasattr(processor, "commission_rate")
    assert 0 <= processor.commission_rate <= 1
