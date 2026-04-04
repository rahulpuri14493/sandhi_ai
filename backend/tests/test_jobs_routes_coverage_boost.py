"""Additional HTTP coverage for api/routes/jobs.py."""

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import api.routes.jobs as jobs_mod
from core.security import create_access_token, get_password_hash
from models.agent import Agent, AgentStatus
from models.job import Job, JobStatus, WorkflowStep
from models.mcp_server import MCPToolConfig, MCPToolType
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


def test_output_contract_template_authenticated_returns_body(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "oct")
    r = client.get("/api/jobs/output-contract/template", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data.get("version") == "1.0"
    assert "write_targets" in data


def test_create_job_rejects_invalid_platform_tool_id(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "ipt")
    r = client.post(
        "/api/jobs",
        data={"title": "T", "allowed_platform_tool_ids": "[999991, 999992]"},
        headers=h,
    )
    assert r.status_code == 400
    assert "platform" in r.text.lower()


def test_create_job_rejects_disallowed_file_extension(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "badext")
    files = {"files": ("malware.exe", io.BytesIO(b"x"), "application/octet-stream")}
    r = client.post("/api/jobs", data={"title": "T"}, files=files, headers=h)
    assert r.status_code == 400
    assert "not allowed" in r.text.lower()


def test_analyze_documents_forbidden_other_business(client: TestClient, db_session):
    owner, _ = _headers_biz(db_session, "ado")
    other, oh = _headers_biz(db_session, "adi")
    job = Job(
        business_id=owner.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps([{"path": "/tmp/x.txt", "name": "x.txt"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=oh)
    assert r.status_code == 403


def test_analyze_documents_no_files_metadata(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "anf")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=None,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=h)
    assert r.status_code == 400
    assert "no documents" in r.text.lower()


def test_analyze_documents_invalid_files_json(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "aifj")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files="not-json-array",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=h)
    assert r.status_code == 400


def test_analyze_documents_no_readable_sources(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "anrs")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps([{"name": "orphan.txt"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=h)
    assert r.status_code == 400
    assert "valid document" in r.text.lower()


def test_answer_question_missing_answer_unprocessable(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "aqm")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps([{"path": "/tmp/x.txt", "name": "x.txt"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.post(f"/api/jobs/{job.id}/answer-question", json={}, headers=h)
    assert r.status_code == 422


def test_answer_question_no_unanswered_question(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "aqnu")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps(
            [{"type": "question", "question": "Q?", "answer": "done"}]
        ),
        files=json.dumps([{"path": "/tmp/x.txt", "name": "x.txt"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.post(
        f"/api/jobs/{job.id}/answer-question", json={"answer": "x"}, headers=h
    )
    assert r.status_code == 400
    assert "unanswered" in r.text.lower()


def test_create_job_inline_schedule_calls_scheduler_add(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "inlsch")
    mock_svc = MagicMock()
    monkeypatch.setattr(jobs_mod, "get_scheduler", lambda: mock_svc)
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = client.post(
        "/api/jobs",
        data={
            "title": "Scheduled create",
            "schedule_scheduled_at": future,
            "schedule_timezone": "UTC",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    mock_svc.add_schedule.assert_called_once()


def test_analyze_documents_ignores_bad_conversation_json(client: TestClient, db_session, tmp_path, monkeypatch):
    u, h = _headers_biz(db_session, "abcj")
    p = tmp_path / "d.txt"
    p.write_text("doc", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        files=json.dumps(
            [{"name": "d.txt", "path": str(p), "type": "text/plain", "size": 3}]
        ),
        conversation="not-valid-json{",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_analyze(**kwargs):
        return {"analysis": "ok", "questions": [], "recommendations": [], "solutions": [], "next_steps": []}

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "analyze_documents_and_generate_questions",
        staticmethod(fake_analyze),
    )
    async def _noop_persist(*a, **k):
        return None

    monkeypatch.setattr(jobs_mod, "persist_brd_analysis_artifact", _noop_persist)

    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=h)
    assert r.status_code == 200


def test_analyze_documents_500_when_analyzer_raises(client: TestClient, db_session, tmp_path, monkeypatch):
    u, h = _headers_biz(db_session, "a500")
    p = tmp_path / "d.txt"
    p.write_text("doc", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        files=json.dumps(
            [{"name": "d.txt", "path": str(p), "type": "text/plain", "size": 3}]
        ),
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def boom(**kwargs):
        raise RuntimeError("analyzer exploded")

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "analyze_documents_and_generate_questions",
        staticmethod(boom),
    )
    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=h)
    assert r.status_code == 500


def test_generate_workflow_questions_404_and_403_and_no_workflow(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "gw404")
    assert client.post("/api/jobs/999777/generate-workflow-questions", headers=h).status_code == 404

    owner, _ = _headers_biz(db_session, "gwo")
    other, oh = _headers_biz(db_session, "gwi")
    job = Job(
        business_id=owner.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=oh).status_code == 403

    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=_headers_biz(db_session, "gwnf")[1])
    # wrong headers — use owner
    r2 = client.post(
        f"/api/jobs/{job.id}/generate-workflow-questions",
        headers={"Authorization": f"Bearer {create_access_token({'sub': owner.id})}"},
    )
    assert r2.status_code == 400
    assert "no workflow" in r2.text.lower()


def test_generate_workflow_questions_no_agent_endpoint(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "gwnae")
    dev = User(
        email=f"d_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="NoEp",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint=None,
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(
        WorkflowStep(
            job_id=job.id,
            agent_id=ag.id,
            step_order=1,
            input_data="{}",
        )
    )
    db_session.commit()
    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=h)
    assert r.status_code == 400
    assert "api endpoint" in r.text.lower()


def test_generate_workflow_skips_step_without_endpoint_and_survives_step_error(
    monkeypatch, client: TestClient, db_session, tmp_path
):
    u, h = _headers_biz(db_session, "gwsk")
    dev = User(
        email=f"d2_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    a_ok = Agent(
        developer_id=dev.id,
        name="A1",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    a_bad = Agent(
        developer_id=dev.id,
        name="A2",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://two.example/v1",
    )
    db_session.add_all([a_ok, a_bad])
    db_session.commit()
    db_session.refresh(a_ok)
    db_session.refresh(a_bad)

    p = tmp_path / "r.txt"
    p.write_text("req", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        files=json.dumps(
            [{"name": "r.txt", "path": str(p), "type": "text/plain", "size": 3}]
        ),
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all(
        [
            WorkflowStep(
                job_id=job.id,
                agent_id=a_ok.id,
                step_order=1,
                input_data=json.dumps({"assigned_task": "t1"}),
            ),
            WorkflowStep(
                job_id=job.id,
                agent_id=a_bad.id,
                step_order=2,
                input_data="{}",
            ),
        ]
    )
    db_session.commit()

    calls = {"n": 0}

    async def fake_gen(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"questions": ["First step Q?"]}
        raise RuntimeError("step2 boom")

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "generate_workflow_clarification_questions",
        staticmethod(fake_gen),
    )
    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=h)
    assert r.status_code == 200
    assert "First step Q?" in r.json()["questions"]


def test_generate_workflow_invalid_files_json_still_runs(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "gwifj")
    dev = User(
        email=f"d3_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="Ag",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        files="NOT_JSON_ARRAY",
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(
        WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}")
    )
    db_session.commit()

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "generate_workflow_clarification_questions",
        staticmethod(lambda **kw: {"questions": []}),
    )
    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=h)
    assert r.status_code == 200


def test_update_job_forbidden_other_business(client: TestClient, db_session):
    owner, _ = _headers_biz(db_session, "upfo")
    other, oh = _headers_biz(db_session, "upfi")
    job = Job(business_id=owner.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.put(f"/api/jobs/{job.id}", data={"title": "X"}, headers=oh)
    assert r.status_code == 403


def test_update_job_non_draft_title_change_rejected(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "upnd")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.APPROVED,
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.put(f"/api/jobs/{job.id}", data={"title": "Nope"}, headers=h)
    assert r.status_code == 400


def test_update_job_invalid_status_value(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "upis")
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.put(
        f"/api/jobs/{job.id}",
        data={"status": "not_a_real_status"},
        headers=h,
    )
    assert r.status_code == 400


def test_create_job_accepts_allowed_platform_tool_ids(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "aptok")
    cfg = MCPToolConfig(
        user_id=u.id,
        tool_type=MCPToolType.MYSQL,
        name="db",
        encrypted_config="{}",
        is_active=True,
    )
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    r = client.post(
        "/api/jobs",
        data={
            "title": "With tools",
            "allowed_platform_tool_ids": json.dumps([cfg.id]),
        },
        headers=h,
    )
    assert r.status_code == 201
    body = r.json()
    assert body.get("allowed_platform_tool_ids") == [cfg.id]


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
