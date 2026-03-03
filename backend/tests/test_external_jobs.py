"""API tests for external job endpoints (share links, token-based access)."""
import json
import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from db.database import get_db
from main import app
from models.user import User, UserRole
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from core.security import get_password_hash, create_access_token


@pytest.fixture
def client_with_data(db_session):
    """Client with business user, job, and workflow step in DB."""
    unique = uuid.uuid4().hex[:8]
    # Create business user (unique email per test to avoid UNIQUE constraint)
    business = User(
        email=f"business-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.BUSINESS,
    )
    db_session.add(business)
    db_session.commit()
    db_session.refresh(business)

    # Create developer and agent (for workflow step)
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
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    # Create job
    job = Job(
        business_id=business.id,
        title="External Test Job",
        description="For external API tests",
        status=JobStatus.COMPLETED,
        files=json.dumps([{"name": "doc.pdf", "type": "application/pdf"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # Create workflow step
    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="completed",
        output_data='{"choices":[{"message":{"content":"Done"}}]}',
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
        yield c, business, job, agent
    app.dependency_overrides.clear()


def test_external_get_job_requires_token(client_with_data):
    """GET /api/external/jobs/{id} returns 401 without token."""
    _, _, job, _ = client_with_data
    client = client_with_data[0]
    response = client.get(f"/api/external/jobs/{job.id}")
    assert response.status_code == 401
    assert "token" in response.json().get("detail", "").lower()


def test_external_get_job_with_query_token(client_with_data):
    """GET /api/external/jobs/{id}?token=xxx returns job when token valid."""
    client, business, job, agent = client_with_data
    from core.external_token import create_job_token

    token = create_job_token(job.id)
    response = client.get(f"/api/external/jobs/{job.id}", params={"token": token})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job.id
    assert data["title"] == "External Test Job"
    assert data["status"] == "completed"
    assert len(data["workflow_steps"]) == 1
    assert data["workflow_steps"][0]["agent_name"] == "Test Agent"


def test_external_get_job_with_header_token(client_with_data):
    """GET /api/external/jobs/{id} with X-Job-Token header works."""
    client, _, job, _ = client_with_data
    from core.external_token import create_job_token

    token = create_job_token(job.id)
    response = client.get(
        f"/api/external/jobs/{job.id}",
        headers={"X-Job-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["id"] == job.id


def test_external_get_job_invalid_token(client_with_data):
    """GET /api/external/jobs/{id} returns 401 for invalid token."""
    client, _, job, _ = client_with_data
    response = client.get(f"/api/external/jobs/{job.id}", params={"token": "invalid"})
    assert response.status_code == 401


def test_external_get_job_not_found(client_with_data):
    """GET /api/external/jobs/99999 returns 404 when job does not exist."""
    client = client_with_data[0]
    from core.external_token import create_job_token

    token = create_job_token(99999)
    response = client.get("/api/external/jobs/99999", params={"token": token})
    assert response.status_code == 404


def test_external_get_status(client_with_data):
    """GET /api/external/jobs/{id}/status returns lightweight status."""
    client, _, job, _ = client_with_data
    from core.external_token import create_job_token

    token = create_job_token(job.id)
    response = client.get(f"/api/external/jobs/{job.id}/status", params={"token": token})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job.id
    assert data["status"] == "completed"
    assert "workflow_steps" in data


@patch("api.routes.external_jobs._get_external_api_key")
def test_external_create_job_requires_api_key(mock_get_key, client_with_data):
    """POST /api/external/jobs returns 401 without valid API key."""
    mock_get_key.return_value = "secret-key"
    client = client_with_data[0]
    response = client.post(
        "/api/external/jobs",
        json={"title": "New Job", "description": "Test"},
    )
    assert response.status_code == 401


@patch("api.routes.external_jobs._get_external_api_key")
def test_external_create_job_success(mock_get_key, client_with_data):
    """POST /api/external/jobs creates job and returns share_url when API key valid."""
    mock_get_key.return_value = "secret-key"
    client = client_with_data[0]
    response = client.post(
        "/api/external/jobs",
        json={"title": "External Created Job", "description": "From API"},
        headers={"X-API-Key": "secret-key"},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["title"] == "External Created Job"
    assert "share_url" in data
    assert "token" in data
    assert "/api/external/jobs/" in data["share_url"]
    assert f"token={data['token']}" in data["share_url"] or data["token"] in data["share_url"]


@patch("api.routes.external_jobs._get_external_api_key")
def test_external_create_job_unconfigured(mock_get_key, client_with_data):
    """POST /api/external/jobs returns 503 when EXTERNAL_API_KEY not set."""
    mock_get_key.return_value = None
    client = client_with_data[0]
    response = client.post(
        "/api/external/jobs",
        json={"title": "New Job"},
        headers={"X-API-Key": "any"},
    )
    assert response.status_code == 503


def test_share_link_requires_auth(client_with_data):
    """GET /api/jobs/{id}/share-link returns 401 when unauthenticated."""
    client, _, job, _ = client_with_data
    response = client.get(f"/api/jobs/{job.id}/share-link")
    assert response.status_code == 401


def test_share_link_returns_url_when_authenticated(client_with_data):
    """GET /api/jobs/{id}/share-link returns share_url when business user authenticated."""
    client, business, job, _ = client_with_data
    token = create_access_token(data={"sub": business.id})
    response = client.get(
        f"/api/jobs/{job.id}/share-link",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == job.id
    assert "share_url" in data
    assert "token" in data
    assert f"/api/external/jobs/{job.id}" in data["share_url"]
