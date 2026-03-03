"""Unit tests for Pydantic schemas."""
import pytest
from datetime import datetime
from pydantic import ValidationError

from schemas.user import UserCreate, UserLogin
from schemas.agent import AgentCreate, AgentUpdate
from models.agent import PricingModel, AgentStatus
from schemas.job import (
    JobCreate,
    JobUpdate,
    WorkflowStepCreate,
    WorkflowStepResponse,
    WorkflowPreview,
)
from models.job import JobStatus


def test_job_create_valid():
    """JobCreate accepts valid input."""
    data = JobCreate(title="Test Job", description="A test")
    assert data.title == "Test Job"
    assert data.description == "A test"
    assert data.agent_ids == []
    assert data.workflow_steps == []


def test_job_create_with_agent_ids():
    """JobCreate accepts agent_ids."""
    data = JobCreate(title="Job", agent_ids=[1, 2, 3])
    assert data.agent_ids == [1, 2, 3]


def test_job_create_title_required():
    """JobCreate requires title."""
    with pytest.raises(ValidationError):
        JobCreate(description="No title")


def test_job_update_partial():
    """JobUpdate accepts partial updates."""
    data = JobUpdate(title="Updated")
    assert data.title == "Updated"
    assert data.description is None
    assert data.status is None


def test_job_update_status():
    """JobUpdate accepts valid status."""
    data = JobUpdate(status=JobStatus.APPROVED)
    assert data.status == JobStatus.APPROVED


def test_workflow_step_create_valid():
    """WorkflowStepCreate accepts valid input."""
    data = WorkflowStepCreate(agent_id=1, step_order=1)
    assert data.agent_id == 1
    assert data.step_order == 1
    assert data.input_data is None


def test_workflow_step_create_with_input_data():
    """WorkflowStepCreate accepts input_data."""
    input_data = {"key": "value"}
    data = WorkflowStepCreate(agent_id=1, step_order=1, input_data=input_data)
    assert data.input_data == input_data


def test_workflow_step_response_from_attributes():
    """WorkflowStepResponse can be created from ORM-like object."""
    class MockStep:
        id = 1
        job_id = 1
        agent_id = 1
        step_order = 1
        input_data = None
        output_data = None
        status = "completed"
        cost = 5.0
        started_at = datetime.utcnow()
        completed_at = datetime.utcnow()

    step = WorkflowStepResponse.model_validate(MockStep())
    assert step.id == 1
    assert step.job_id == 1
    assert step.agent_id == 1
    assert step.cost == 5.0
    assert step.status == "completed"


def test_workflow_preview_valid():
    """WorkflowPreview accepts valid breakdown."""
    preview = WorkflowPreview(
        steps=[],
        total_cost=10.0,
        breakdown={
            "task_costs": 8.0,
            "communication_costs": 1.0,
            "commission": 1.0,
        },
    )
    assert preview.total_cost == 10.0
    assert preview.breakdown["task_costs"] == 8.0
    assert preview.breakdown["communication_costs"] == 1.0
    assert preview.breakdown["commission"] == 1.0


def test_user_create_valid():
    """UserCreate accepts valid email and role."""
    data = UserCreate(email="user@test.com", password="secret123", role="business")
    assert data.email == "user@test.com"
    assert data.password == "secret123"
    assert data.role == "business"


def test_user_login_valid():
    """UserLogin accepts email and password."""
    data = UserLogin(email="login@test.com", password="mypass")
    assert data.email == "login@test.com"
    assert data.password == "mypass"


def test_agent_create_valid():
    """AgentCreate accepts valid input."""
    data = AgentCreate(
        name="My Agent",
        description="Test agent",
        price_per_task=5.0,
        price_per_communication=0.5,
    )
    assert data.name == "My Agent"
    assert data.price_per_task == 5.0
    assert data.pricing_model == PricingModel.PAY_PER_USE


def test_agent_create_with_api_endpoint():
    """AgentCreate accepts api_endpoint and api_key."""
    data = AgentCreate(
        name="API Agent",
        api_endpoint="https://api.example.com",
        api_key="sk-xxx",
    )
    assert data.api_endpoint == "https://api.example.com"
    assert data.api_key == "sk-xxx"


def test_agent_update_partial():
    """AgentUpdate accepts partial updates."""
    data = AgentUpdate(name="Updated Name", price_per_task=10.0)
    assert data.name == "Updated Name"
    assert data.price_per_task == 10.0
    assert data.description is None
