import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from core.security import get_password_hash
from db.database import get_db
from main import app
from models.agent import Agent
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole


@pytest.fixture
def internal_secret():
    return "test-internal-secret-123"


@pytest.fixture
def client_internal_exec(db_session, internal_secret):
    from core import config

    original_secret = getattr(config.settings, "MCP_INTERNAL_SECRET", None)
    config.settings.MCP_INTERNAL_SECRET = internal_secret

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, db_session
    app.dependency_overrides.clear()
    if original_secret is not None:
        config.settings.MCP_INTERNAL_SECRET = original_secret
    else:
        config.settings.MCP_INTERNAL_SECRET = ""


def _seed_job_with_step(db_session):
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"exec-internal-biz-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.BUSINESS,
    )
    dev = User(
        email=f"exec-internal-dev-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.DEVELOPER,
    )
    db_session.add_all([business, dev])
    db_session.commit()
    db_session.refresh(business)
    db_session.refresh(dev)

    agent = Agent(
        developer_id=dev.id,
        name="Execution Agent",
        description="Test",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://agent.example.com",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = Job(
        business_id=business.id,
        title="Heartbeat Job",
        description="Testing internal execution telemetry route",
        status=JobStatus.IN_PROGRESS,
        execution_token="tok-abc",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="in_progress",
        started_at=datetime.utcnow(),
        live_phase="calling_tool",
        live_reason_code="tool_call_result",
        live_reason_detail='{"tool_name":"platform_1_db","elapsed_ms":2200}',
        live_trace_id="trace-1",
    )
    db_session.add(step)
    db_session.commit()
    db_session.refresh(step)
    return job, step


def test_internal_execution_live_requires_secret(client_internal_exec):
    client, _db = client_internal_exec
    r = client.get("/api/internal/execution/jobs/1/steps/live")
    assert r.status_code == 403


def test_internal_execution_live_returns_db_fallback_when_redis_missing(client_internal_exec, internal_secret, monkeypatch):
    client, db_session = client_internal_exec
    job, step = _seed_job_with_step(db_session)

    import api.routes.execution_internal as route_mod

    monkeypatch.setattr(route_mod, "get_step_live_state", lambda **_: None)

    r = client.get(
        f"/api/internal/execution/jobs/{job.id}/steps/live",
        headers={"X-Internal-Secret": internal_secret},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == job.id
    assert len(data["steps"]) == 1
    row = data["steps"][0]
    assert row["workflow_step_id"] == step.id
    assert row["live_source"] == "db_fallback"
    assert row["live"]["phase"] == "calling_tool"
    assert row["live"]["reason_code"] == "tool_call_result"
    assert row["live"]["reason_detail"]["tool_name"] == "platform_1_db"
    # list endpoint defaults to compact mode
    assert "reason_detail_json" not in row["live"]


def test_internal_execution_live_prefers_redis_payload(client_internal_exec, internal_secret, monkeypatch):
    client, db_session = client_internal_exec
    job, step = _seed_job_with_step(db_session)

    import api.routes.execution_internal as route_mod

    monkeypatch.setattr(
        route_mod,
        "get_step_live_state",
        lambda **_: {"phase": "calling_agent", "reason_code": "agent_call_start", "message": "attempt 1/2"},
    )

    r = client.get(
        f"/api/internal/execution/jobs/{job.id}/steps/live",
        headers={"X-Internal-Secret": internal_secret},
    )
    assert r.status_code == 200
    row = r.json()["steps"][0]
    assert row["live_source"] == "redis"
    assert row["live"]["phase"] == "calling_agent"
    assert row["live"]["reason_code"] == "agent_call_start"


def test_internal_execution_single_step_live_success(client_internal_exec, internal_secret, monkeypatch):
    client, db_session = client_internal_exec
    job, step = _seed_job_with_step(db_session)

    import api.routes.execution_internal as route_mod

    monkeypatch.setattr(
        route_mod,
        "get_step_live_state",
        lambda **_: {"phase": "calling_tool", "reason_code": "tool_call_start", "message": "querying tool"},
    )

    r = client.get(
        f"/api/internal/execution/jobs/{job.id}/steps/{step.id}/live",
        headers={"X-Internal-Secret": internal_secret},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == job.id
    assert data["step"]["workflow_step_id"] == step.id
    assert data["step"]["live_source"] == "redis"
    assert data["step"]["live"]["reason_code"] == "tool_call_start"


def test_internal_execution_single_step_full_mode_includes_reason_detail_json(
    client_internal_exec, internal_secret, monkeypatch
):
    client, db_session = client_internal_exec
    job, step = _seed_job_with_step(db_session)

    import api.routes.execution_internal as route_mod

    monkeypatch.setattr(route_mod, "get_step_live_state", lambda **_: None)
    r = client.get(
        f"/api/internal/execution/jobs/{job.id}/steps/{step.id}/live?compact=false",
        headers={"X-Internal-Secret": internal_secret},
    )
    assert r.status_code == 200
    live = r.json()["step"]["live"]
    assert "reason_detail_json" in live


def test_internal_execution_single_step_live_requires_secret(client_internal_exec):
    client, db_session = client_internal_exec
    job, step = _seed_job_with_step(db_session)
    r = client.get(f"/api/internal/execution/jobs/{job.id}/steps/{step.id}/live")
    assert r.status_code == 403


def test_internal_execution_single_step_live_not_found(client_internal_exec, internal_secret):
    client, db_session = client_internal_exec
    job, _step = _seed_job_with_step(db_session)
    r = client.get(
        f"/api/internal/execution/jobs/{job.id}/steps/999999/live",
        headers={"X-Internal-Secret": internal_secret},
    )
    assert r.status_code == 404
