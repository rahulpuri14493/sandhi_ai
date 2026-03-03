"""API tests for agents endpoints."""
import uuid
import pytest
from fastapi.testclient import TestClient

from db.database import get_db
from main import app
from models.user import User, UserRole
from models.agent import Agent, AgentStatus
from core.security import get_password_hash, create_access_token


@pytest.fixture
def client_with_agents(db_session):
    """Client with developer user and agents in DB."""
    unique = uuid.uuid4().hex[:8]
    dev = User(
        email=f"dev-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    agent1 = Agent(
        developer_id=dev.id,
        name="Test Agent 1",
        description="First agent",
        price_per_task=5.0,
        price_per_communication=0.5,
        status=AgentStatus.ACTIVE,
    )
    agent2 = Agent(
        developer_id=dev.id,
        name="Test Agent 2",
        description="Second agent",
        price_per_task=10.0,
        price_per_communication=1.0,
        status=AgentStatus.ACTIVE,
    )
    db_session.add_all([agent1, agent2])
    db_session.commit()
    db_session.refresh(agent1)
    db_session.refresh(agent2)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, dev, agent1, agent2
    app.dependency_overrides.clear()


def test_list_agents_public(client_with_agents):
    """GET /api/agents returns active agents without auth."""
    client, _, agent1, agent2 = client_with_agents
    response = client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    names = [a["name"] for a in data]
    assert "Test Agent 1" in names
    assert "Test Agent 2" in names
    for a in data:
        assert "api_key" not in a or a.get("api_key") is None


def test_list_agents_filter_by_status(client_with_agents):
    """GET /api/agents?status=active returns only active agents."""
    client, _, _, _ = client_with_agents
    response = client.get("/api/agents", params={"status": "active"})
    assert response.status_code == 200
    data = response.json()
    assert all(a["status"] == "active" for a in data)


def test_get_agent_requires_auth(client_with_agents):
    """GET /api/agents/{id} returns 401 without token."""
    client, _, agent1, _ = client_with_agents
    response = client.get(f"/api/agents/{agent1.id}")
    assert response.status_code == 401


def test_get_agent_success(client_with_agents):
    """GET /api/agents/{id} returns agent when authenticated."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    response = client.get(
        f"/api/agents/{agent1.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == agent1.id
    assert data["name"] == "Test Agent 1"
    assert data["price_per_task"] == 5.0


def test_get_agent_not_found(client_with_agents):
    """GET /api/agents/99999 returns 404 when agent does not exist."""
    client, dev, _, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    response = client.get(
        "/api/agents/99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
