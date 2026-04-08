"""Coverage-oriented tests for large API modules (jobs/hiring/dashboards)."""


from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from models.agent import Agent, AgentStatus, PricingModel
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole


def _make_user(db_session, *, role: UserRole, email: str) -> tuple[User, str]:
    user = User(email=email, password_hash=get_password_hash("pw123456"), role=role)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.id})
    return user, token


def test_jobs_create_get_update_and_share_link(client: TestClient, db_session):
    business, token = _make_user(db_session, role=UserRole.BUSINESS, email="biz@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create job (multipart form)
    resp = client.post(
        "/api/jobs",
        data={"title": "Test Job", "description": "Desc", "tool_visibility": "full"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    job = resp.json()
    job_id = job["id"]
    assert job["business_id"] == business.id
    assert job["tool_visibility"] in ("full", None)

    # Get job
    resp = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == job_id

    # Update job title + tool_visibility
    resp = client.put(
        f"/api/jobs/{job_id}",
        data={"title": "Updated Job", "tool_visibility": "names_only"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Updated Job"
    assert resp.json().get("tool_visibility") == "names_only"

    # Share link
    resp = client.get(f"/api/jobs/{job_id}/share-link", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_id"] == job_id
    assert "token" in data and data["token"]
    assert "share_url" in data and data["share_url"]


def test_hiring_position_nomination_and_review_flow(client: TestClient, db_session):
    business, biz_token = _make_user(db_session, role=UserRole.BUSINESS, email="biz2@example.com")
    developer, dev_token = _make_user(db_session, role=UserRole.DEVELOPER, email="dev@example.com")

    biz_headers = {"Authorization": f"Bearer {biz_token}"}
    dev_headers = {"Authorization": f"Bearer {dev_token}"}

    # Developer owns an agent (needed for nominations)
    agent = Agent(
        developer_id=developer.id,
        name="Dev Agent",
        description="d",
        capabilities=["x"],
        input_schema={},
        output_schema={},
        pricing_model=PricingModel.PAY_PER_USE,
        price_per_task=1.0,
        price_per_communication=0.1,
        status=AgentStatus.PENDING,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    # Business creates a hiring position
    resp = client.post(
        "/api/hiring/positions",
        json={"title": "Role", "description": "Need agent", "requirements": "Do stuff"},
        headers=biz_headers,
    )
    assert resp.status_code == 201, resp.text
    pos = resp.json()
    pos_id = pos["id"]
    assert pos["business_id"] == business.id

    # Anyone can list positions (defaults to OPEN)
    resp = client.get("/api/hiring/positions", headers=biz_headers)
    assert resp.status_code == 200
    assert any(p["id"] == pos_id for p in resp.json())

    # Developer nominates their agent
    resp = client.post(
        "/api/hiring/nominations",
        json={"hiring_position_id": pos_id, "agent_id": agent.id, "cover_letter": "Pick me"},
        headers=dev_headers,
    )
    assert resp.status_code == 201, resp.text
    nom = resp.json()
    nom_id = nom["id"]
    assert nom["agent_id"] == agent.id
    assert nom["developer_id"] == developer.id

    # Business lists nominations (scoped to their positions)
    resp = client.get("/api/hiring/nominations", headers=biz_headers)
    assert resp.status_code == 200
    assert any(n["id"] == nom_id for n in resp.json())

    # Business approves nomination; should activate agent
    resp = client.put(
        f"/api/hiring/nominations/{nom_id}/review",
        json={"status": "approved", "review_notes": "ok"},
        headers=biz_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    db_session.refresh(agent)
    assert agent.status == AgentStatus.ACTIVE


def test_dashboards_basic_endpoints(client: TestClient, db_session):
    business, biz_token = _make_user(db_session, role=UserRole.BUSINESS, email="biz3@example.com")
    developer, dev_token = _make_user(db_session, role=UserRole.DEVELOPER, email="dev2@example.com")
    biz_headers = {"Authorization": f"Bearer {biz_token}"}
    dev_headers = {"Authorization": f"Bearer {dev_token}"}

    # Business jobs/spending (empty baseline)
    resp = client.get("/api/businesses/jobs", headers=biz_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/businesses/spending", headers=biz_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_count"] >= 0

    # Developer stats/agents/earnings (empty baseline)
    resp = client.get("/api/developers/stats", headers=dev_headers)
    assert resp.status_code == 200
    assert "agent_count" in resp.json()

    resp = client.get("/api/developers/agents", headers=dev_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/developers/earnings", headers=dev_headers)
    assert resp.status_code == 200
    assert "total_earnings" in resp.json()


def test_business_agent_performance_endpoint(client: TestClient, db_session):
    business, biz_token = _make_user(db_session, role=UserRole.BUSINESS, email="biz-perf@example.com")
    developer, _dev_token = _make_user(db_session, role=UserRole.DEVELOPER, email="dev-perf@example.com")
    biz_headers = {"Authorization": f"Bearer {biz_token}"}

    agent = Agent(
        developer_id=developer.id,
        name="Perf Agent",
        description="Perf",
        capabilities=["analyze"],
        input_schema={},
        output_schema={},
        pricing_model=PricingModel.PAY_PER_USE,
        price_per_task=2.5,
        price_per_communication=0.2,
        status=AgentStatus.ACTIVE,
        api_endpoint="https://agent.perf.example.com",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = Job(
        business_id=business.id,
        title="Perf Job",
        description="Perf",
        status=JobStatus.COMPLETED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="completed",
        cost=2.5,
        output_data='{"agent_output":{"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18},"confidence":0.92}}',
        live_phase="completed",
        live_reason_code="step_completed",
        live_reason_detail='{"kind":"agent_call","elapsed_ms":1234}',
    )
    db_session.add(step)
    db_session.commit()

    resp = client.get("/api/businesses/agents/performance", headers=biz_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["business_id"] == business.id
    assert len(data["agents"]) >= 1
    first = data["agents"][0]
    assert first["agent_id"] == agent.id
    assert first["totals"]["total_tokens"] >= 18
    assert first["quality"]["success_rate"] >= 0.0

