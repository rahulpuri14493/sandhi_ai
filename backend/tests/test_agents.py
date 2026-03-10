"""API tests for agents endpoints and agent reviews."""
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


def test_list_agents_includes_average_rating_and_review_count(client_with_agents):
    """GET /api/agents returns average_rating and review_count for marketplace cards."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 4, "review_text": "Good"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    agent1_data = next(a for a in data if a["id"] == agent1.id)
    assert "average_rating" in agent1_data
    assert "review_count" in agent1_data
    assert agent1_data["average_rating"] == 4.0
    assert agent1_data["review_count"] == 1


def test_list_agents_filter_by_status(client_with_agents):
    """GET /api/agents?status=active returns only active agents."""
    client, _, _, _ = client_with_agents
    response = client.get("/api/agents", params={"status": "active"})
    assert response.status_code == 200
    data = response.json()
    assert all(a["status"] == "active" for a in data)


def test_get_agent_public_no_auth(client_with_agents):
    """GET /api/agents/{id} without token: 200 but api_key and api_endpoint are hidden (logged-in only)."""
    client, _, agent1, _ = client_with_agents
    response = client.get(f"/api/agents/{agent1.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == agent1.id
    assert data["name"] == "Test Agent 1"
    assert data.get("api_key") is None
    assert data.get("api_endpoint") is None


def test_get_agent_returns_api_key_for_owner(client_with_agents):
    """GET /api/agents/{id} returns api_key and api_endpoint when requester is authenticated (owner sees api_key)."""
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
    assert "api_key" in data
    # api_endpoint is returned for any authenticated user
    assert "api_endpoint" in data


def test_get_agent_returns_api_endpoint_when_authenticated(client_with_agents, db_session):
    """GET /api/agents/{id} returns api_endpoint when user is logged in (any valid user)."""
    client, dev, agent1, _ = client_with_agents
    agent1.api_endpoint = "https://api.example.com/v1/chat"
    db_session.commit()
    token = create_access_token(data={"sub": dev.id})
    response = client.get(
        f"/api/agents/{agent1.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["api_endpoint"] == "https://api.example.com/v1/chat"


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
    client, _, _, _ = client_with_agents
    response = client.get("/api/agents/99999")
    assert response.status_code == 404


# ---------- Agent reviews ----------


def test_reviews_summary_public(client_with_agents):
    """GET /api/agents/{id}/reviews/summary returns without auth."""
    client, _, agent1, _ = client_with_agents
    response = client.get(f"/api/agents/{agent1.id}/reviews/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == agent1.id
    assert data["average_rating"] == 0.0
    assert data["total_count"] == 0
    # JSON object keys are always strings
    assert data["rating_distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}


def test_reviews_list_public(client_with_agents):
    """GET /api/agents/{id}/reviews returns paginated list without auth."""
    client, _, agent1, _ = client_with_agents
    response = client.get(f"/api/agents/{agent1.id}/reviews", params={"limit": 10, "offset": 0})
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert data["total"] == 0
    assert data["limit"] == 10
    assert data["offset"] == 0
    assert len(data["items"]) == 0


def test_reviews_summary_after_review(client_with_agents):
    """Summary reflects average and count after a review is created."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 4, "review_text": "Good agent."},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = client.get(f"/api/agents/{agent1.id}/reviews/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == agent1.id
    assert data["average_rating"] == 4.0
    assert data["total_count"] == 1


def test_post_review_requires_auth(client_with_agents):
    """POST /api/agents/{id}/reviews returns 401 without token."""
    client, _, agent1, _ = client_with_agents
    response = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 5, "review_text": "Great"},
    )
    assert response.status_code == 401


def test_post_review_success(client_with_agents):
    """POST /api/agents/{id}/reviews creates review when authenticated."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    response = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 5, "review_text": "Excellent agent."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["agent_id"] == agent1.id
    assert data["user_id"] == dev.id
    assert data["rating"] == 5.0
    assert data["review_text"] == "Excellent agent."
    assert data["is_own"] is True


def test_post_review_without_text(client_with_agents):
    """POST /api/agents/{id}/reviews accepts rating only; review text is optional."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    response = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["agent_id"] == agent1.id
    assert data["rating"] == 2.0
    assert data["review_text"] is None


def test_post_review_with_empty_string_text(client_with_agents):
    """POST with review_text empty string is accepted and stored as null."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    response = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 3, "review_text": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["rating"] == 3.0
    assert data["review_text"] is None


def test_post_review_multiple(client_with_agents):
    """Same user can submit multiple reviews per agent."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    r1 = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 3, "review_text": "First"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 5, "review_text": "Second."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 201
    summary = client.get(f"/api/agents/{agent1.id}/reviews/summary")
    assert summary.json()["total_count"] == 2
    assert summary.json()["average_rating"] == 4.0


def test_post_review_validation_rating(client_with_agents):
    """POST with rating out of range returns 422."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    response = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 6, "review_text": "Too high"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_update_review_by_id_success(client_with_agents):
    """PUT /api/agents/{id}/reviews/{review_id} updates own review."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    create = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 3, "review_text": "Original"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    review_id = create.json()["id"]
    response = client.put(
        f"/api/agents/{agent1.id}/reviews/{review_id}",
        json={"rating": 5, "review_text": "Updated."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rating"] == 5.0
    assert data["review_text"] == "Updated."


def test_delete_review_by_id_success(client_with_agents):
    """DELETE /api/agents/{id}/reviews/{review_id} removes own review."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    create = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 4},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    review_id = create.json()["id"]
    response = client.delete(
        f"/api/agents/{agent1.id}/reviews/{review_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204
    summary = client.get(f"/api/agents/{agent1.id}/reviews/summary")
    assert summary.json()["total_count"] == 0


def test_reviews_404_for_nonexistent_agent(client_with_agents):
    """Review endpoints return 404 when agent does not exist."""
    client, dev, _, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    r = client.get("/api/agents/99999/reviews/summary")
    assert r.status_code == 404
    r = client.post(
        "/api/agents/99999/reviews",
        json={"rating": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_update_review_forbidden_not_owner(client_with_agents, db_session):
    """PUT /api/agents/{id}/reviews/{review_id} returns 403 when not the review author."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    create = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 4, "review_text": "Mine"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    review_id = create.json()["id"]
    other = User(
        email="other-user@test.com",
        password_hash=get_password_hash("other"),
        role=UserRole.BUSINESS,
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    other_token = create_access_token(data={"sub": other.id})
    response = client.put(
        f"/api/agents/{agent1.id}/reviews/{review_id}",
        json={"rating": 1},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 403
    assert "Not authorized" in response.json().get("detail", "")


def test_delete_review_forbidden_not_owner(client_with_agents, db_session):
    """DELETE /api/agents/{id}/reviews/{review_id} returns 403 when not the review author."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    create = client.post(
        f"/api/agents/{agent1.id}/reviews",
        json={"rating": 4},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    review_id = create.json()["id"]
    other = User(
        email="other-delete@test.com",
        password_hash=get_password_hash("other"),
        role=UserRole.BUSINESS,
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    other_token = create_access_token(data={"sub": other.id})
    response = client.delete(
        f"/api/agents/{agent1.id}/reviews/{review_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 403
    assert "Not authorized" in response.json().get("detail", "")


def test_a2a_card_not_found(client_with_agents):
    """GET /api/agents/{id}/a2a-card returns 404 when agent does not exist."""
    client, _, _, _ = client_with_agents
    r = client.get("/api/agents/99999/a2a-card")
    assert r.status_code == 404


def test_a2a_card_no_endpoint_returns_400(client_with_agents):
    """GET /api/agents/{id}/a2a-card returns 400 when agent has no API endpoint."""
    client, _, agent1, _ = client_with_agents
    assert agent1.api_endpoint is None
    r = client.get(f"/api/agents/{agent1.id}/a2a-card")
    assert r.status_code == 400
    assert "api endpoint" in r.json().get("detail", "").lower()


def test_a2a_card_success(client_with_agents):
    """GET /api/agents/{id}/a2a-card returns A2A Agent Card when agent has endpoint."""
    client, dev, agent1, _ = client_with_agents
    token = create_access_token(data={"sub": dev.id})
    # Update agent to have an endpoint (via API)
    client.put(
        f"/api/agents/{agent1.id}",
        json={"api_endpoint": "https://agent.example.com/a2a", "capabilities": ["nlp", "code"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    r = client.get(f"/api/agents/{agent1.id}/a2a-card")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == agent1.name
    assert data["url"] == "https://agent.example.com/a2a"
    assert "capabilities" in data
    assert "protocolVersion" in data
    assert "authentication" in data
