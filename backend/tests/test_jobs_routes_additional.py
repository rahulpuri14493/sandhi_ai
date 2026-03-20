"""Additional coverage tests for api/routes/jobs.py (mocked analyzer)."""

import asyncio
import io
import json
import uuid

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from models.user import User, UserRole
from models.job import Job, JobStatus
from models.agent import Agent, AgentStatus
from models.job import WorkflowStep


def _make_business(db_session, email_suffix: str = None) -> tuple[User, dict]:
    suffix = email_suffix or uuid.uuid4().hex[:8]
    user = User(email=f"biz_jobs_{suffix}@example.com", password_hash=get_password_hash("pw123456"), role=UserRole.BUSINESS)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.id})
    return user, {"Authorization": f"Bearer {token}"}


def test_create_job_rejects_invalid_tool_visibility(client: TestClient, db_session):
    _, headers = _make_business(db_session)
    r = client.post("/api/jobs", data={"title": "T", "tool_visibility": "bad"}, headers=headers)
    assert r.status_code == 400


def test_create_job_rejects_invalid_file_extension(client: TestClient, db_session):
    _, headers = _make_business(db_session)
    f = io.BytesIO(b"hello")
    r = client.post(
        "/api/jobs",
        data={"title": "T"},
        files={"files": ("a.exe", f, "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "not allowed" in r.text.lower()


def test_create_job_rejects_bad_zip(client: TestClient, db_session):
    _, headers = _make_business(db_session)
    f = io.BytesIO(b"not-a-zip")
    r = client.post(
        "/api/jobs",
        data={"title": "T"},
        files={"files": ("a.zip", f, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "zip" in r.text.lower()


def test_process_one_upload_zip_s3_stages_raw_zip_then_deletes(monkeypatch):
    """In S3 mode, zip is staged first, extracted, then raw zip object is removed."""
    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.settings, "OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(jobs_mod.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 1)

    # Build an in-memory zip with one allowed file.
    buf = io.BytesIO()
    with jobs_mod.zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok.txt", "hello")
    zip_bytes = buf.getvalue()

    calls = {"persisted": [], "deleted": [], "downloaded": 0}

    async def fake_persist(name, data, content_type, *, job_id=None):
        calls["persisted"].append(name)
        if name.endswith(".zip"):
            return {
                "id": "zip-meta",
                "name": name,
                "storage": "s3",
                "bucket": "b",
                "key": "jobs/1/raw.zip",
                "type": content_type,
                "size": len(data),
            }
        return {
            "id": f"doc-{name}",
            "name": name,
            "storage": "s3",
            "bucket": "b",
            "key": f"jobs/1/{name}",
            "type": content_type,
            "size": len(data),
        }

    async def fake_delete(file_info):
        calls["deleted"].append(file_info.get("id"))

    def fake_download(file_info):
        calls["downloaded"] += 1
        assert file_info.get("id") == "zip-meta"
        return zip_bytes

    monkeypatch.setattr(jobs_mod, "persist_file", fake_persist)
    monkeypatch.setattr(jobs_mod, "delete_file", fake_delete)
    monkeypatch.setattr(jobs_mod, "download_s3_bytes", fake_download)

    upload = UploadFile(filename="bundle.zip", file=io.BytesIO(zip_bytes))
    out = asyncio.run(jobs_mod._process_one_upload(upload, job_id=1))

    assert len(out) == 1
    assert out[0]["name"] == "ok.txt"
    assert calls["persisted"][0] == "bundle.zip"  # raw zip staged first
    assert calls["downloaded"] == 1  # extraction source is MinIO object bytes
    assert "zip-meta" in calls["deleted"]  # raw zip removed after extraction


def test_process_one_upload_zip_s3_cleans_up_raw_zip_when_download_fails(monkeypatch):
    """If staged zip cannot be downloaded from S3, staged raw zip is deleted."""
    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.settings, "OBJECT_STORAGE_BACKEND", "s3")
    calls = {"deleted": []}

    async def fake_persist(name, data, content_type, *, job_id=None):
        return {
            "id": "zip-meta",
            "name": name,
            "storage": "s3",
            "bucket": "b",
            "key": "jobs/1/raw.zip",
            "type": content_type,
            "size": len(data),
        }

    async def fake_delete(file_info):
        calls["deleted"].append(file_info.get("id"))

    def fake_download(_file_info):
        raise RuntimeError("download failed")

    monkeypatch.setattr(jobs_mod, "persist_file", fake_persist)
    monkeypatch.setattr(jobs_mod, "delete_file", fake_delete)
    monkeypatch.setattr(jobs_mod, "download_s3_bytes", fake_download)

    upload = UploadFile(filename="bundle.zip", file=io.BytesIO(b"dummy"))
    with pytest.raises(jobs_mod.HTTPException) as exc:
        asyncio.run(jobs_mod._process_one_upload(upload, job_id=1))

    assert exc.value.status_code == 500
    assert "Failed to stage zip file" in exc.value.detail
    assert calls["deleted"] == ["zip-meta"]


def test_process_one_upload_zip_s3_retries_and_cleans_staged_entries(monkeypatch):
    """On transient extract failure, staged extracted files are cleaned before retry."""
    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.settings, "OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(jobs_mod.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(jobs_mod.settings, "ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(jobs_mod.settings, "ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(jobs_mod.settings, "ZIP_EXTRACT_RETRY_JITTER_SECONDS", 0.0)

    # Build zip with two extractable files so one can stage before failure.
    buf = io.BytesIO()
    with jobs_mod.zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "A")
        zf.writestr("b.txt", "B")
    zip_bytes = buf.getvalue()

    state = {"attempt": 1}
    calls = {"deleted": []}

    async def fake_persist(name, data, content_type, *, job_id=None):
        if name.endswith(".zip"):
            return {
                "id": "zip-meta",
                "name": name,
                "storage": "s3",
                "bucket": "b",
                "key": "jobs/1/raw.zip",
                "type": content_type,
                "size": len(data),
            }
        if name == "a.txt" and state["attempt"] == 1:
            return {
                "id": "doc-a-attempt1",
                "name": name,
                "storage": "s3",
                "bucket": "b",
                "key": "jobs/1/a.txt",
                "type": content_type,
                "size": len(data),
            }
        if name == "b.txt" and state["attempt"] == 1:
            state["attempt"] = 2
            raise RuntimeError("transient persist failure")
        return {
            "id": f"doc-{name}-attempt2",
            "name": name,
            "storage": "s3",
            "bucket": "b",
            "key": f"jobs/1/{name}",
            "type": content_type,
            "size": len(data),
        }

    async def fake_delete(file_info):
        calls["deleted"].append(file_info.get("id"))

    def fake_download(file_info):
        assert file_info.get("id") == "zip-meta"
        return zip_bytes

    monkeypatch.setattr(jobs_mod, "persist_file", fake_persist)
    monkeypatch.setattr(jobs_mod, "delete_file", fake_delete)
    monkeypatch.setattr(jobs_mod, "download_s3_bytes", fake_download)

    upload = UploadFile(filename="bundle.zip", file=io.BytesIO(zip_bytes))
    out = asyncio.run(jobs_mod._process_one_upload(upload, job_id=1))

    assert sorted([x["name"] for x in out]) == ["a.txt", "b.txt"]
    # Failed first attempt should clean staged extracted file.
    assert "doc-a-attempt1" in calls["deleted"]
    # Raw zip should be removed only after overall success.
    assert "zip-meta" in calls["deleted"]


def test_analyze_documents_success_adds_completion(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _make_business(db_session)

    # Create a real temp file and store path in job.files (as app does).
    p = tmp_path / "req.txt"
    p.write_text("Req", encoding="utf-8")

    job = Job(
        business_id=biz.id,
        title="T",
        description="D",
        status=JobStatus.DRAFT,
        files=json.dumps([{"id": "f1", "name": "req.txt", "path": str(p), "type": "text/plain", "size": 3}]),
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_analyze(**kwargs):
        return {"analysis": "A", "questions": [], "recommendations": ["R"], "solutions": ["S"], "next_steps": ["N"]}

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.DocumentAnalyzer, "analyze_documents_and_generate_questions", staticmethod(fake_analyze))

    r = client.post(f"/api/jobs/{job.id}/analyze-documents", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["analysis"] == "A"
    assert data["questions"] == []
    assert any(item.get("type") == "completion" for item in data["conversation"])


def test_answer_question_happy_path(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _make_business(db_session)
    p = tmp_path / "req.txt"
    p.write_text("Req", encoding="utf-8")

    job = Job(
        business_id=biz.id,
        title="T",
        description="D",
        status=JobStatus.DRAFT,
        files=json.dumps([{"id": "f1", "name": "req.txt", "path": str(p), "type": "text/plain", "size": 3}]),
        conversation=json.dumps([{"type": "question", "question": "Q1?", "answer": None}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_process(*args, **kwargs):
        return {"analysis": "A2", "questions": ["Q2?"], "recommendations": [], "solutions": [], "next_steps": []}

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.DocumentAnalyzer, "process_user_response", staticmethod(fake_process))

    r = client.post(f"/api/jobs/{job.id}/answer-question", json={"answer": "Ans"}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["analysis"] == "A2"
    assert out["questions"] == ["Q2?"]


def test_delete_job_rejects_in_progress(client: TestClient, db_session):
    biz, headers = _make_business(db_session)
    job = Job(business_id=biz.id, title="T", status=JobStatus.IN_PROGRESS, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.delete(f"/api/jobs/{job.id}", headers=headers)
    assert r.status_code == 400
    assert "cannot delete" in r.text.lower()


def test_workflow_preview_returns_200(client: TestClient, db_session):
    """GET /api/jobs/{id}/workflow/preview returns WorkflowPreview."""
    biz, headers = _make_business(db_session)
    job = Job(business_id=biz.id, title="Preview Job", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/workflow/preview", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert "steps" in data and "total_cost" in data


def test_update_job_with_new_brd_clears_old_workflow_and_resets_to_draft(client: TestClient, db_session):
    biz, headers = _make_business(db_session, "updclear")
    dev = User(
        email=f"dev_jobs_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    # Existing completed job with one workflow step from old BRD.
    old_file = {"id": "f1", "name": "old.txt", "path": "/tmp/old.txt", "type": "text/plain", "size": 3}
    job = Job(
        business_id=biz.id,
        title="Completed Job",
        description="Old BRD flow",
        status=JobStatus.COMPLETED,
        total_cost=12.5,
        files=json.dumps([old_file]),
        conversation=json.dumps([{"type": "analysis", "content": "old analysis"}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    agent = Agent(
        developer_id=dev.id,
        name="Old Agent",
        description="A",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1/chat/completions",
        api_key="sk-test",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        input_data=json.dumps({"assigned_task": "old task"}),
        output_data=json.dumps({"result": "old"}),
        status="completed",
    )
    db_session.add(step)
    db_session.commit()

    # Upload replacement BRD/document.
    new_file = io.BytesIO(b"new requirement content")
    r = client.put(
        f"/api/jobs/{job.id}",
        files={"files": ("new.txt", new_file, "text/plain")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "draft"
    assert out["files"] and len(out["files"]) == 1
    # Steps should be cleared, forcing workflow rebuild for new BRD.
    assert out["workflow_steps"] == []


def test_generate_workflow_questions_no_questions_sets_flag(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _make_business(db_session, "wfq1")
    dev = User(
        email=f"dev_jobs_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    p = tmp_path / "req.txt"
    p.write_text("Simple requirement", encoding="utf-8")
    job = Job(
        business_id=biz.id,
        title="WFQ Job",
        description="D",
        status=JobStatus.DRAFT,
        files=json.dumps([{"id": "f1", "name": "req.txt", "path": str(p), "type": "text/plain", "size": 18}]),
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    agent = Agent(
        developer_id=dev.id,
        name="Dev Agent",
        description="A",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1/chat/completions",
        api_key="sk-test",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        input_data=json.dumps({"assigned_task": "Solve requirement"}),
        status="pending",
    )
    db_session.add(step)
    db_session.commit()

    async def fake_generate(**kwargs):
        return {"questions": []}

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "generate_workflow_clarification_questions",
        staticmethod(fake_generate),
    )

    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["questions"] == []
    assert out["added_questions"] == []
    assert out["no_questions_needed"] is True
    assert "conversation" in out


def test_generate_workflow_questions_returns_added_questions_only(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _make_business(db_session, "wfq2")
    dev = User(
        email=f"dev_jobs_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    p = tmp_path / "req.txt"
    p.write_text("Need clarification", encoding="utf-8")
    existing_q = "What is the target number?"
    job = Job(
        business_id=biz.id,
        title="WFQ Job 2",
        description="D",
        status=JobStatus.DRAFT,
        files=json.dumps([{"id": "f1", "name": "req.txt", "path": str(p), "type": "text/plain", "size": 18}]),
        conversation=json.dumps([{"type": "question", "question": existing_q, "answer": None}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    agent = Agent(
        developer_id=dev.id,
        name="Dev Agent 2",
        description="A",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1/chat/completions",
        api_key="sk-test",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        input_data=json.dumps({"assigned_task": "Clarify requirement"}),
        status="pending",
    )
    db_session.add(step)
    db_session.commit()

    async def fake_generate(**kwargs):
        # Returns one duplicate existing question and one new one
        return {"questions": [existing_q, "Should we round the result?"]}

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "generate_workflow_clarification_questions",
        staticmethod(fake_generate),
    )

    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["questions"] == [existing_q, "Should we round the result?"]
    assert out["added_questions"] == ["Should we round the result?"]
    assert out["no_questions_needed"] is False
    assert sum(1 for i in out["conversation"] if i.get("type") == "question") == 2


def test_generate_workflow_questions_prunes_stale_unanswered_when_none_needed(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _make_business(db_session, "wfq3")
    dev = User(
        email=f"dev_jobs_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    p = tmp_path / "req.txt"
    p.write_text("Simple requirement", encoding="utf-8")
    job = Job(
        business_id=biz.id,
        title="WFQ Job 3",
        description="D",
        status=JobStatus.DRAFT,
        files=json.dumps([{"id": "f1", "name": "req.txt", "path": str(p), "type": "text/plain", "size": 18}]),
        conversation=json.dumps(
            [
                {"type": "analysis", "content": "Existing analysis"},
                {"type": "question", "question": "Old unresolved question?", "answer": None},
                {"type": "question", "question": "Already answered", "answer": "yes"},
            ]
        ),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    agent = Agent(
        developer_id=dev.id,
        name="Dev Agent 3",
        description="A",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1/chat/completions",
        api_key="sk-test",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        input_data=json.dumps({"assigned_task": "Clarify requirement"}),
        status="pending",
    )
    db_session.add(step)
    db_session.commit()

    async def fake_generate(**kwargs):
        return {"questions": []}

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod.DocumentAnalyzer,
        "generate_workflow_clarification_questions",
        staticmethod(fake_generate),
    )

    r = client.post(f"/api/jobs/{job.id}/generate-workflow-questions", headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["no_questions_needed"] is True
    assert out["added_questions"] == []
    assert out["removed_unanswered_questions"] == 1
    assert all(not (i.get("type") == "question" and not str(i.get("answer", "")).strip()) for i in out["conversation"])

