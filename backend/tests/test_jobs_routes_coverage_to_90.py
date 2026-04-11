"""Targeted HTTP/unit-style tests to raise api.routes.jobs coverage above 90%."""

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette import status as starlette_status

import api.routes.jobs as jobs_mod
from core.security import create_access_token, get_password_hash
from models.agent import Agent, AgentStatus
from models.communication import AgentCommunication
from models.job import Job, JobStatus, JobSchedule, ScheduleStatus, WorkflowStep
from models.mcp_server import MCPToolConfig, MCPToolType
from models.transaction import Earnings, EarningsStatus, Transaction, TransactionStatus
from models.user import User, UserRole


def _headers_biz(db_session, suffix: str | None = None):
    s = suffix or uuid.uuid4().hex[:8]
    u = User(
        email=f"j90_{s}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u, {"Authorization": f"Bearer {create_access_token({'sub': u.id})}"}


def test_create_job_rollback_after_partial_upload(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "rbk")
    monkeypatch.setattr(jobs_mod, "delete_file", AsyncMock())
    n = 0

    async def flaky_upload(file, job_id=None):
        nonlocal n
        n += 1
        if n == 1:
            return [{"id": "1", "name": "a.txt", "path": "/tmp/a"}]
        from fastapi import HTTPException as H
        from starlette import status as st

        raise H(status_code=st.HTTP_500_INTERNAL_SERVER_ERROR, detail="second upload fails")

    monkeypatch.setattr(jobs_mod, "_process_one_upload", flaky_upload)
    files = [
        ("files", ("a.txt", io.BytesIO(b"a"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"b"), "text/plain")),
    ]
    r = client.post("/api/jobs", data={"title": "RollbackT"}, files=files, headers=h)
    assert r.status_code == 500
    assert db_session.query(Job).filter(Job.title == "RollbackT").first() is None


def test_create_job_inline_schedule_past_date_keeps_draft(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "past")
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    r = client.post(
        "/api/jobs",
        data={
            "title": "PastSch",
            "schedule_scheduled_at": past,
            "schedule_timezone": "UTC",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"


def test_analyze_documents_dedupes_questions_and_completion_branch(
    monkeypatch, client: TestClient, db_session, tmp_path
):
    u, h = _headers_biz(db_session, "aded")
    p = tmp_path / "d.txt"
    p.write_text("body", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps(
            [{"type": "question", "question": "DupQ", "answer": None}]
        ),
        files=json.dumps(
            [{"name": "d.txt", "path": str(p), "type": "text/plain", "size": 4}]
        ),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_analyze(**kwargs):
        return {
            "analysis": "A",
            "questions": ["DupQ", "NewQ"],
            "recommendations": [],
            "solutions": [],
            "next_steps": [],
        }

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
    conv = r.json()["conversation"]
    qs = [x for x in conv if x.get("type") == "question" and x.get("question") == "NewQ"]
    assert len(qs) == 1

    async def fake_analyze_completion(**kwargs):
        return {
            "analysis": "",
            "questions": [],
            "recommendations": ["r1"],
            "solutions": ["s1"],
            "next_steps": ["n1"],
        }

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "analyze_documents_and_generate_questions",
        staticmethod(fake_analyze_completion),
    )
    job2 = Job(
        business_id=u.id,
        title="J2",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps(
            [{"name": "d.txt", "path": str(p), "type": "text/plain", "size": 4}]
        ),
    )
    db_session.add(job2)
    db_session.commit()
    db_session.refresh(job2)
    r2 = client.post(f"/api/jobs/{job2.id}/analyze-documents", headers=h)
    assert r2.status_code == 200
    types = [x.get("type") for x in r2.json()["conversation"]]
    assert "completion" in types


def test_answer_question_404_403_no_files(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "a404")
    assert (
        client.post("/api/jobs/999888/answer-question", json={"answer": "x"}, headers=h).status_code
        == 404
    )
    owner, _ = _headers_biz(db_session, "ao")
    other, oh = _headers_biz(db_session, "ai")
    job = Job(
        business_id=owner.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps([{"path": "/x", "name": "f.txt"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert (
        client.post(f"/api/jobs/{job.id}/answer-question", json={"answer": "x"}, headers=oh).status_code
        == 403
    )
    job2 = Job(
        business_id=u.id,
        title="J2",
        status=JobStatus.DRAFT,
        conversation=json.dumps(
            [{"type": "question", "question": "Q?", "answer": None}]
        ),
        files=None,
    )
    db_session.add(job2)
    db_session.commit()
    db_session.refresh(job2)
    r = client.post(f"/api/jobs/{job2.id}/answer-question", json={"answer": "x"}, headers=h)
    assert r.status_code == 400


def test_answer_question_hired_agent_completion_and_analysis_dedupe(
    monkeypatch, client: TestClient, db_session, tmp_path
):
    u, h = _headers_biz(db_session, "ahir")
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
        name="H",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://agent.example/v1",
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    p = tmp_path / "f.txt"
    p.write_text("t", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps(
            [
                {"type": "analysis", "content": "Same", "timestamp": "1"},
                {"type": "question", "question": "Q1?", "answer": None},
            ]
        ),
        files=json.dumps(
            [{"name": "f.txt", "path": str(p), "type": "text/plain", "size": 1}]
        ),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(
        WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}")
    )
    db_session.commit()

    async def proc(**kwargs):
        return {
            "questions": [],
            "recommendations": [],
            "solutions": [],
            "next_steps": [],
            "analysis": "Same",
        }

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "process_user_response",
        staticmethod(proc),
    )
    r = client.post(f"/api/jobs/{job.id}/answer-question", json={"answer": "yes"}, headers=h)
    assert r.status_code == 200
    conv = r.json()["conversation"]
    analyses = [x for x in conv if x.get("type") == "analysis"]
    assert len(analyses) == 1

    async def proc2(**kwargs):
        return {
            "questions": ["Q1?", "Fresh"],
            "recommendations": [],
            "analysis": "New block",
        }

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "process_user_response",
        staticmethod(proc2),
    )
    job2 = Job(
        business_id=u.id,
        title="J3",
        status=JobStatus.DRAFT,
        conversation=json.dumps(
            [{"type": "question", "question": "Open?", "answer": None}]
        ),
        files=json.dumps(
            [{"name": "f.txt", "path": str(p), "type": "text/plain", "size": 1}]
        ),
    )
    db_session.add(job2)
    db_session.commit()
    db_session.refresh(job2)
    db_session.add(
        WorkflowStep(job_id=job2.id, agent_id=ag.id, step_order=1, input_data="{}")
    )
    db_session.commit()
    r2 = client.post(f"/api/jobs/{job2.id}/answer-question", json={"answer": "ok"}, headers=h)
    assert r2.status_code == 200
    qtexts = [
        x.get("question")
        for x in r2.json()["conversation"]
        if x.get("type") == "question"
    ]
    assert "Fresh" in qtexts
    assert qtexts.count("Q1?") <= 1


def test_answer_question_process_raises_500(monkeypatch, client: TestClient, db_session, tmp_path):
    u, h = _headers_biz(db_session, "a5")
    p = tmp_path / "t.txt"
    p.write_text("x", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps(
            [{"type": "question", "question": "Q?", "answer": None}]
        ),
        files=json.dumps(
            [{"name": "t.txt", "path": str(p), "type": "text/plain", "size": 1}]
        ),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "process_user_response",
        staticmethod(boom),
    )
    r = client.post(f"/api/jobs/{job.id}/answer-question", json={"answer": "a"}, headers=h)
    assert r.status_code == 500


def test_generate_workflow_skips_step_logs_and_read_file_failure(
    monkeypatch, client: TestClient, db_session, tmp_path
):
    monkeypatch.setattr("services.planner_llm.is_agent_planner_configured", lambda: True)
    u, h = _headers_biz(db_session, "gwsk2")
    dev = User(
        email=f"d_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    a_skip = Agent(
        developer_id=dev.id,
        name="NoEp",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint=None,
    )
    a_ok = Agent(
        developer_id=dev.id,
        name="Ok",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    db_session.add_all([a_skip, a_ok])
    db_session.commit()
    db_session.refresh(a_skip)
    db_session.refresh(a_ok)
    p = tmp_path / "r.txt"
    p.write_text("r", encoding="utf-8")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        files=json.dumps(
            [{"name": "r.txt", "path": str(p), "type": "text/plain", "size": 1}]
        ),
        conversation=json.dumps(
            [{"type": "question", "question": "Stale?", "answer": None}]
        ),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all(
        [
            WorkflowStep(
                job_id=job.id,
                agent_id=a_skip.id,
                step_order=1,
                input_data="not-json",
            ),
            WorkflowStep(
                job_id=job.id,
                agent_id=a_ok.id,
                step_order=2,
                input_data="{}",
            ),
        ]
    )
    db_session.commit()

    async def bad_read(self, f):
        raise OSError("unreadable")

    monkeypatch.setattr(jobs_mod.DocumentAnalyzer, "read_file_info", bad_read)

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "generate_workflow_clarification_questions",
        staticmethod(lambda **kw: {"questions": []}),
    )
    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body.get("removed_unanswered_questions", 0) >= 1


def test_planner_status_endpoint(monkeypatch, client: TestClient, db_session):
    _, h = _headers_biz(db_session, "plst")
    monkeypatch.setattr(
        "services.planner_llm.is_agent_planner_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "services.planner_llm.get_planner_public_meta",
        lambda: {"planner_model": "test-model"},
    )
    r = client.get("/api/jobs/planner/status", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data.get("configured") is True
    assert data.get("planner_model") == "test-model"


def test_list_jobs_parses_malformed_json_safely(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "ljmj")
    job = Job(
        business_id=u.id,
        title="BadJson",
        status=JobStatus.DRAFT,
        conversation="not-json{",
        files="also[bad",
        allowed_platform_tool_ids="{not a list",
        allowed_connection_ids="oops",
    )
    db_session.add(job)
    db_session.commit()
    r = client.get("/api/jobs", headers=h)
    assert r.status_code == 200
    row = next(x for x in r.json() if x["title"] == "BadJson")
    assert row.get("files") is None
    assert row.get("conversation") is None


def test_get_job_malformed_step_tool_json(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "gjm")
    dev = User(
        email=f"dv_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
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
        conversation=json.dumps([]),
        allowed_platform_tool_ids="not-json",
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
            allowed_platform_tool_ids="{bad",
            allowed_connection_ids="[",
        )
    )
    db_session.commit()
    r = client.get(f"/api/jobs/{job.id}", headers=h)
    assert r.status_code == 200
    steps = r.json()["workflow_steps"]
    assert steps and steps[0].get("allowed_platform_tool_ids") is None


def test_update_job_tool_scope_and_contract(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "upt")
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
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    contract = {"version": "1.0", "write_policy": {"on_write_error": "continue"}}
    r = client.put(
        f"/api/jobs/{job.id}",
        data={
            "allowed_platform_tool_ids": json.dumps([cfg.id]),
            "tool_visibility": "names_only",
            "write_execution_mode": "platform",
            "output_artifact_format": "jsonl",
            "output_contract": json.dumps(contract),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("tool_visibility") == "names_only"
    assert body.get("output_contract") is not None


def test_update_job_upload_resets_workflow_and_auto_analyze(
    monkeypatch, client: TestClient, db_session, tmp_path
):
    u, h = _headers_biz(db_session, "upw")
    dev = User(
        email=f"dv2_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
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
        status=JobStatus.APPROVED,
        conversation=json.dumps([]),
        files=json.dumps([{"id": "old", "name": "old.txt", "path": "/tmp/old.txt"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    st = WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}")
    db_session.add(st)
    db_session.commit()
    db_session.refresh(st)
    db_session.add(
        AgentCommunication(
            from_workflow_step_id=st.id,
            to_workflow_step_id=st.id,
            from_agent_id=ag.id,
            to_agent_id=ag.id,
            data_transferred="{}",
        )
    )
    db_session.commit()

    p = tmp_path / "new.txt"
    p.write_text("new content", encoding="utf-8")

    async def fake_proc(file, job_id=None):
        return [
            {
                "id": "n1",
                "name": "new.txt",
                "path": str(p),
                "type": "text/plain",
                "size": 11,
            }
        ]

    monkeypatch.setattr(jobs_mod, "_process_one_upload", fake_proc)

    async def fake_analyze(**kwargs):
        return {"analysis": "auto", "questions": ["Q?"], "recommendations": [], "solutions": [], "next_steps": []}

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "analyze_documents_and_generate_questions",
        staticmethod(fake_analyze),
    )
    async def _noop_persist(*a, **k):
        return None

    monkeypatch.setattr(jobs_mod, "persist_brd_analysis_artifact", _noop_persist)

    files = {"files": ("new.txt", io.BytesIO(b"new content"), "text/plain")}
    r = client.put(
        f"/api/jobs/{job.id}",
        data={"status": "draft"},
        files=files,
        headers=h,
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).count() == 0
    job_db = db_session.query(Job).filter(Job.id == job.id).first()
    assert job_db.status == JobStatus.DRAFT
    conv = json.loads(job_db.conversation or "[]")
    assert any(x.get("type") == "analysis" for x in conv)


def test_update_job_upload_failure_cleans_staged(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "upf")
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    deleted = []

    async def boom(file, job_id=None):
        return [{"id": "s1", "name": "a.txt", "path": "/x"}]

    async def second(file, job_id=None):
        raise HTTPException(
            status_code=starlette_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="fail mid-loop",
        )

    calls = {"n": 0}

    async def router(file, job_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return await boom(file, job_id=job_id)
        return await second(file, job_id=job_id)

    monkeypatch.setattr(jobs_mod, "_process_one_upload", router)

    async def track_delete(entry):
        deleted.append(entry)

    monkeypatch.setattr(jobs_mod, "delete_file", track_delete)

    files = [
        ("files", ("a.txt", io.BytesIO(b"a"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"b"), "text/plain")),
    ]
    r = client.put(f"/api/jobs/{job.id}", files=files, headers=h)
    assert r.status_code == 500
    assert deleted


def test_delete_job_cleans_transaction_and_steps(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "djt")
    job = Job(business_id=u.id, title="J", status=JobStatus.COMPLETED, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    dev = User(
        email=f"dv3_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    st = WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}")
    db_session.add(st)
    db_session.commit()
    db_session.refresh(st)
    txn = Transaction(
        job_id=job.id,
        payer_id=u.id,
        total_amount=10.0,
        platform_commission=1.0,
        status=TransactionStatus.COMPLETED,
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    db_session.add(
        Earnings(
            developer_id=dev.id,
            transaction_id=txn.id,
            amount=5.0,
            status=EarningsStatus.PAID,
        )
    )
    db_session.commit()

    r = client.delete(f"/api/jobs/{job.id}", headers=h)
    assert r.status_code == 204
    assert db_session.query(Job).filter(Job.id == job.id).first() is None


def test_approve_forbidden_and_malformed_json_in_response(client: TestClient, db_session):
    owner, _ = _headers_biz(db_session, "apf")
    other, oh = _headers_biz(db_session, "apo")
    job = Job(
        business_id=owner.id,
        title="J",
        status=JobStatus.DRAFT,
        files="not-json",
        conversation="{",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert client.post(f"/api/jobs/{job.id}/approve", headers=oh).status_code == 403
    r = client.post(
        f"/api/jobs/{job.id}/approve",
        headers={"Authorization": f"Bearer {create_access_token({'sub': owner.id})}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending_approval"


def test_execute_forbidden_wrong_status_and_queue_failure(
    monkeypatch, client: TestClient, db_session
):
    u, h = _headers_biz(db_session, "exf")
    owner, _ = _headers_biz(db_session, "exo")
    job = Job(
        business_id=owner.id,
        title="J",
        status=JobStatus.PENDING_APPROVAL,
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert client.post(f"/api/jobs/{job.id}/execute", headers=h).status_code == 403

    job2 = Job(
        business_id=u.id,
        title="J2",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
    )
    db_session.add(job2)
    db_session.commit()
    db_session.refresh(job2)
    assert client.post(f"/api/jobs/{job2.id}/execute", headers=h).status_code == 400

    job3 = Job(
        business_id=u.id,
        title="J3",
        status=JobStatus.PENDING_APPROVAL,
        conversation=json.dumps([]),
    )
    db_session.add(job3)
    db_session.commit()
    db_session.refresh(job3)

    def boom(**kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(jobs_mod, "queue_job_execution", boom)
    r = client.post(f"/api/jobs/{job3.id}/execute", headers=h)
    assert r.status_code == 503
    db_session.refresh(job3)
    assert job3.status == JobStatus.PENDING_APPROVAL


def test_execute_success_parses_files(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "exok")
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.PENDING_APPROVAL,
        files=json.dumps([{"id": "1", "name": "a.txt"}]),
        conversation=json.dumps([{"type": "analysis", "content": "x"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    monkeypatch.setattr(jobs_mod, "queue_job_execution", lambda **kw: None)
    r = client.post(f"/api/jobs/{job.id}/execute", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"


def test_share_link_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "sh404")
    r = client.get("/api/jobs/999777/share-link", headers=h)
    assert r.status_code == 404


def test_get_job_status_malformed_json_and_stuck_flag(
    monkeypatch, client: TestClient, db_session
):
    u, h = _headers_biz(db_session, "gst")
    monkeypatch.setattr(jobs_mod.settings, "STUCK_JOB_THRESHOLD_HOURS", 1)
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.IN_PROGRESS,
        files="not-json",
        conversation="bad",
        allowed_platform_tool_ids="{",
        allowed_connection_ids="[",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    sch = JobSchedule(
        job_id=job.id,
        status=ScheduleStatus.ACTIVE,
        timezone="UTC",
        scheduled_at=datetime.utcnow() + timedelta(days=1),
        last_run_time=datetime.utcnow() - timedelta(hours=5),
    )
    db_session.add(sch)
    db_session.commit()
    r = client.get(f"/api/jobs/{job.id}/status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body.get("show_cancel_option") is True


def test_download_job_file_local_and_s3_and_errors(
    monkeypatch, client: TestClient, db_session, tmp_path
):
    u, h = _headers_biz(db_session, "dlf")
    p = tmp_path / "keep.txt"
    p.write_text("hi", encoding="utf-8")
    fid = str(uuid.uuid4())
    job = Job(
        business_id=u.id,
        title="J",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps(
            [
                {
                    "id": fid,
                    "name": "keep.txt",
                    "path": str(p),
                    "type": "text/plain",
                }
            ]
        ),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    r = client.get(f"/api/jobs/{job.id}/files/{fid}", headers=h)
    assert r.status_code == 200

    job2 = Job(
        business_id=u.id,
        title="J2",
        status=JobStatus.DRAFT,
        conversation=json.dumps([]),
        files=json.dumps(
            [
                {
                    "id": "s3id",
                    "name": "x.bin",
                    "storage": "s3",
                    "bucket": "b",
                    "key": "k",
                }
            ]
        ),
    )
    db_session.add(job2)
    db_session.commit()
    db_session.refresh(job2)

    class Body:
        def iter_chunks(self, chunk_size=1024):
            yield b"ab"

    monkeypatch.setattr(
        jobs_mod,
        "open_s3_download_stream",
        lambda fi: (Body(), "application/octet-stream", 2),
    )
    r2 = client.get(f"/api/jobs/{job2.id}/files/s3id", headers=h)
    assert r2.status_code == 200

    monkeypatch.setattr(
        jobs_mod,
        "open_s3_download_stream",
        lambda fi: (_ for _ in ()).throw(RuntimeError("s3")),
    )
    assert client.get(f"/api/jobs/{job2.id}/files/s3id", headers=h).status_code == 404

    assert client.get(f"/api/jobs/{job.id}/files/nope", headers=h).status_code == 404


def test_schedules_list_filter_params_and_create_calls_scheduler(
    monkeypatch, client: TestClient, db_session
):
    u, h = _headers_biz(db_session, "sch")
    mock_svc = MagicMock()
    monkeypatch.setattr(jobs_mod, "get_scheduler", lambda: mock_svc)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    when = (datetime.utcnow() + timedelta(days=5)).isoformat() + "Z"
    r = client.post(
        f"/api/jobs/{job.id}/schedule",
        headers=h,
        json={"scheduled_at": when, "timezone": "UTC"},
    )
    assert r.status_code == 201, r.text
    mock_svc.add_schedule.assert_called_once()

    r_list = client.get(
        f"/api/jobs/schedules/all?job_id={job.id}&sort=oldest&schedule_status=active&job_status=in_queue",
        headers=h,
    )
    assert r_list.status_code == 200
    assert r_list.json().get("total", 0) >= 1


def test_get_schedule_job_not_found(client: TestClient, db_session):
    _, h = _headers_biz(db_session, "gsnf")
    r = client.get("/api/jobs/999666/schedule", headers=h)
    assert r.status_code == 404


def test_put_schedule_inactive_calls_remove(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "puti")
    mock_svc = MagicMock()
    monkeypatch.setattr(jobs_mod, "get_scheduler", lambda: mock_svc)
    job = Job(business_id=u.id, title="J", status=JobStatus.IN_QUEUE, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    sch = JobSchedule(
        job_id=job.id,
        status=ScheduleStatus.ACTIVE,
        timezone="UTC",
        scheduled_at=datetime.utcnow() + timedelta(days=1),
        next_run_time=datetime.utcnow() + timedelta(days=1),
    )
    db_session.add(sch)
    db_session.commit()
    db_session.refresh(sch)
    r = client.put(
        f"/api/jobs/{job.id}/schedule",
        headers=h,
        json={"status": "inactive"},
    )
    assert r.status_code == 200, r.text
    mock_svc.remove_schedule.assert_called_once_with(sch.id)


def test_rerun_not_found_bad_status_enqueue_fail(
    monkeypatch, client: TestClient, db_session
):
    u, h = _headers_biz(db_session, "rru")
    assert client.post("/api/jobs/999555/rerun", headers=h).status_code == 404
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert client.post(f"/api/jobs/{job.id}/rerun", headers=h).status_code == 400

    dev = User(
        email=f"dv4_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    job2 = Job(
        business_id=u.id,
        title="J2",
        status=JobStatus.FAILED,
        conversation=json.dumps([]),
    )
    db_session.add(job2)
    db_session.commit()
    db_session.refresh(job2)
    db_session.add(WorkflowStep(job_id=job2.id, agent_id=ag.id, step_order=1, input_data="{}"))
    sch = JobSchedule(
        job_id=job2.id,
        status=ScheduleStatus.INACTIVE,
        timezone="UTC",
        scheduled_at=datetime.utcnow() + timedelta(days=1),
    )
    db_session.add(sch)
    db_session.commit()
    db_session.refresh(sch)

    def boom(**kwargs):
        raise RuntimeError("no worker")

    monkeypatch.setattr(jobs_mod, "queue_job_execution", boom)
    r = client.post(f"/api/jobs/{job2.id}/rerun", headers=h)
    assert r.status_code == 503


def test_rerun_transition_race_returns_400(monkeypatch, client: TestClient, db_session):
    u, h = _headers_biz(db_session, "rrr")
    dev = User(
        email=f"dv5_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
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
        title="Jr",
        status=JobStatus.FAILED,
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}"))
    db_session.commit()

    monkeypatch.setattr(jobs_mod, "_transition_job_status_if_current", lambda *a, **kw: False)
    r = client.post(f"/api/jobs/{job.id}/rerun", headers=h)
    assert r.status_code == 400


def test_suggest_workflow_tools_filters_platform_and_bad_conversation(
    monkeypatch, client: TestClient, db_session
):
    u, h = _headers_biz(db_session, "swf")
    cfg_keep = MCPToolConfig(
        user_id=u.id,
        tool_type=MCPToolType.MYSQL,
        name="keep",
        encrypted_config="{}",
        is_active=True,
    )
    cfg_other = MCPToolConfig(
        user_id=u.id,
        tool_type=MCPToolType.MYSQL,
        name="other",
        encrypted_config="{}",
        is_active=True,
    )
    db_session.add_all([cfg_keep, cfg_other])
    db_session.commit()
    db_session.refresh(cfg_keep)
    db_session.refresh(cfg_other)
    dev = User(
        email=f"dv6_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
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
        conversation="not-json",
        allowed_platform_tool_ids=json.dumps([cfg_keep.id]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_suggest(**kwargs):
        pts = kwargs.get("platform_tools") or []
        assert all(t.id == cfg_keep.id for t in pts)
        return {"step_suggestions": [], "output_contract_stub": None, "fallback_used": False}

    monkeypatch.setattr(jobs_mod, "suggest_tool_assignments_for_agents", fake_suggest)
    r = client.post(
        f"/api/jobs/{job.id}/suggest-workflow-tools",
        headers=h,
        json={"agent_ids": [ag.id]},
    )
    assert r.status_code == 200


def test_auto_split_not_found_forbidden_invalid_mode(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "asnf")
    assert (
        client.post(
            "/api/jobs/999444/workflow/auto-split",
            headers=h,
            json={"agent_ids": [1]},
        ).status_code
        == 404
    )
    owner, _ = _headers_biz(db_session, "aso")
    other, oh = _headers_biz(db_session, "asi")
    job = Job(business_id=owner.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert (
        client.post(
            f"/api/jobs/{job.id}/workflow/auto-split",
            headers=oh,
            json={"agent_ids": [1]},
        ).status_code
        == 403
    )
    r = client.post(
        f"/api/jobs/{job.id}/workflow/auto-split",
        headers={"Authorization": f"Bearer {create_access_token({'sub': owner.id})}"},
        json={"agent_ids": [1], "workflow_mode": "invalid"},
    )
    assert r.status_code == 400


def test_patch_workflow_step_malformed_json_in_db(client: TestClient, db_session):
    u, h = _headers_biz(db_session, "pwf")
    dev = User(
        email=f"dv7_{uuid.uuid4().hex[:8]}@e.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    ag = Agent(
        developer_id=dev.id,
        name="G",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://ex.com/v1",
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    st = WorkflowStep(
        job_id=job.id,
        agent_id=ag.id,
        step_order=1,
        input_data="{}",
        allowed_platform_tool_ids="{bad json",
        allowed_connection_ids="[[",
    )
    db_session.add(st)
    db_session.commit()
    db_session.refresh(st)
    r = client.patch(
        f"/api/jobs/{job.id}/workflow/steps/{st.id}",
        headers=h,
        json={"tool_visibility": "none"},
    )
    assert r.status_code == 200
    assert r.json().get("allowed_platform_tool_ids") is None

