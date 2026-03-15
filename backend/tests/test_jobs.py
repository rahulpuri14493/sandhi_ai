"""API tests for jobs endpoints (create, update, workflow, tool_visibility, allowed tools)."""
import json
import uuid
import pytest
from fastapi.testclient import TestClient

from db.database import get_db
from main import app
from models.user import User, UserRole
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from core.security import get_password_hash, create_access_token


@pytest.fixture
def client_with_job(db_session):
    """Client with business user, job (with allowed tools), and workflow step."""
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"biz-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.BUSINESS,
    )
    db_session.add(business)
    db_session.commit()
    db_session.refresh(business)

    dev = User(
        email=f"dev-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    agent = Agent(
        developer_id=dev.id,
        name="Test Agent",
        description="Test",
        price_per_task=5.0,
        price_per_communication=0.5,
        api_endpoint="https://agent.example.com",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = Job(
        business_id=business.id,
        title="Jobs API Test",
        description="For testing",
        status=JobStatus.DRAFT,
        allowed_platform_tool_ids=json.dumps([1, 2]),
        allowed_connection_ids=json.dumps([1]),
        tool_visibility="names_only",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="pending",
        depends_on_previous=False,
        allowed_platform_tool_ids=json.dumps([1]),
        tool_visibility="full",
    )
    db_session.add(step)
    db_session.commit()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, business, job, agent, step
    app.dependency_overrides.clear()


def _auth_headers(business):
    token = create_access_token(data={"sub": business.id})
    return {"Authorization": f"Bearer {token}"}


def test_jobs_create_with_tool_visibility(client_with_job):
    """POST /api/jobs with tool_visibility persists it."""
    client, business, *_ = client_with_job
    response = client.post(
        "/api/jobs",
        data={
            "title": "New Job With Tools",
            "description": "Desc",
            "tool_visibility": "none",
        },
        headers=_auth_headers(business),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "New Job With Tools"
    assert data.get("tool_visibility") == "none"


def test_jobs_get_status_returns_parsed_allowed_lists(client_with_job):
    """GET /api/jobs/{id}/status returns allowed_platform_tool_ids and allowed_connection_ids as arrays."""
    client, business, job, *_ = client_with_job
    response = client.get(
        f"/api/jobs/{job.id}/status",
        headers=_auth_headers(business),
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data.get("allowed_platform_tool_ids"), list)
    assert data["allowed_platform_tool_ids"] == [1, 2]
    assert isinstance(data.get("allowed_connection_ids"), list)
    assert data["allowed_connection_ids"] == [1]
    assert data.get("tool_visibility") == "names_only"
    steps = data.get("workflow_steps") or []
    if steps:
        assert steps[0].get("tool_visibility") == "full"


def test_jobs_auto_split_accepts_tool_visibility(client_with_job):
    """POST /api/jobs/{id}/workflow/auto-split with tool_visibility and step_tools.tool_visibility."""
    client, business, job, agent, _ = client_with_job
    response = client.post(
        f"/api/jobs/{job.id}/workflow/auto-split",
        json={
            "agent_ids": [agent.id],
            "workflow_mode": "sequential",
            "tool_visibility": "names_only",
            "step_tools": [{"agent_index": 0, "tool_visibility": "none"}],
        },
        headers=_auth_headers(business),
    )
    assert response.status_code == 200
    data = response.json()
    assert "steps" in data
    assert len(data["steps"]) >= 1
    assert data["steps"][0].get("tool_visibility") == "none"


def test_jobs_update_step_tools_accepts_tool_visibility(client_with_job):
    """PATCH /api/jobs/{id}/workflow/steps/{step_id} with tool_visibility."""
    client, business, job, _, step = client_with_job
    response = client.patch(
        f"/api/jobs/{job.id}/workflow/steps/{step.id}",
        json={"tool_visibility": "none"},
        headers=_auth_headers(business),
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("tool_visibility") == "none"
