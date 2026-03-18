"""Additional coverage tests for api/routes/jobs.py (mocked analyzer)."""

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from models.user import User, UserRole
from models.job import Job, JobStatus


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

