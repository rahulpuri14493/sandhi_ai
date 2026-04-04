"""Additional HTTP coverage for api/routes/jobs.py."""

import json
import uuid

from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from models.agent import Agent, AgentStatus
from models.job import Job, JobStatus
from models.user import User, UserRole


def _headers_biz(db_session, suffix: str | None = None):
    s = suffix or uuid.uuid4().hex[:8]
    u = User(
        email=f"jb_{s}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u, {"Authorization": f"Bearer {create_access_token({'sub': u.id})}"}


def test_get_job_by_id_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "gjnf")
    r = client.get("/api/jobs/999001", headers=h)
    assert r.status_code == 404


def test_get_job_by_id_success(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "gjok")
    job = Job(business_id=u.id, title="T1", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.get(f"/api/jobs/{job.id}", headers=h)
    assert r.status_code == 200
    assert r.json()["title"] == "T1"


def test_share_link_requires_ownership(client: TestClient, db_session):
    owner, _ = _headers_biz(db_session, "slo")
    other, oh = _headers_biz(db_session, "slt")
    job = Job(business_id=owner.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.get(f"/api/jobs/{job.id}/share-link", headers=oh)
    assert r.status_code == 403


def test_job_status_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "stnf")
    r = client.get("/api/jobs/999002/status", headers=h)
    assert r.status_code == 404


def test_update_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "unf")
    r = client.put("/api/jobs/999003", data={"title": "x"}, headers=h)
    assert r.status_code == 404


def test_delete_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "dnf")
    r = client.delete("/api/jobs/999004", headers=h)
    assert r.status_code == 404


def test_list_jobs_business_empty(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "leb")
    r = client.get("/api/jobs", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_approve_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "apnf")
    r = client.post("/api/jobs/999005/approve", headers=h)
    assert r.status_code == 404


def test_execute_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "exnf")
    r = client.post("/api/jobs/999006/execute", headers=h)
    assert r.status_code == 404


def test_manual_workflow_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "mwnf")
    r = client.post("/api/jobs/999007/workflow/manual", json=[], headers=h)
    assert r.status_code == 404


def test_workflow_preview_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "wpnf")
    r = client.get("/api/jobs/999008/workflow/preview", headers=h)
    assert r.status_code == 404


def test_analyze_documents_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "adnf")
    r = client.post("/api/jobs/999009/analyze-documents", headers=h)
    assert r.status_code == 404


def test_suggest_workflow_tools_post_no_platform_tools_returns_fallback(client: TestClient, db_session):
    """Hits early return in suggest_workflow_tools when business has no MCP tools."""
    u, h = _headers_biz(db_session, "swtfb")
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    dev = User(
        email=f"dev_sw_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    agent = Agent(
        developer_id=dev.id,
        name="A",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    r = client.post(
        f"/api/jobs/{job.id}/suggest-workflow-tools",
        headers=h,
        json={"agent_ids": [agent.id]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("fallback_used") is True
    assert data.get("step_suggestions") == []
