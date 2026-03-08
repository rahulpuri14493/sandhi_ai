"""Integration test: create job with a BRD document from uploads, run analyze-documents, assert job flow works.

BRD files are expected in uploads/jobs (project root) or backend/uploads/jobs.
When running tests in Docker, mount project uploads so the backend sees them:
  docker compose run --rm --no-deps -v ./uploads:/app/uploads backend pytest tests/test_job_flow_brd.py -v
"""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.database import get_db
from main import app
from models.user import User, UserRole
from core.security import get_password_hash, create_access_token


# Possible locations for BRD docs: backend root (e.g. /app/uploads/jobs in Docker) or project root uploads/jobs.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_ROOT.parent
_CANDIDATE_UPLOADS = [
    _BACKEND_ROOT / "uploads" / "jobs",
    _PROJECT_ROOT / "uploads" / "jobs",
]


def _find_brd_file():
    """Return path to first .docx in uploads/jobs (backend or project root), or None."""
    for uploads_jobs in _CANDIDATE_UPLOADS:
        if uploads_jobs.is_dir():
            for p in uploads_jobs.iterdir():
                if p.is_file() and p.suffix.lower() == ".docx":
                    return p
    return None


@pytest.fixture
def client_with_business(db_session):
    """Test client with a business user and auth token."""
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"business-brd-{unique}@test.com",
        password_hash=get_password_hash("testpass"),
        role=UserRole.BUSINESS,
    )
    db_session.add(business)
    db_session.commit()
    db_session.refresh(business)
    token = create_access_token(data={"sub": business.id})
    headers = {"Authorization": f"Bearer {token}"}

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, headers, business
    app.dependency_overrides.clear()


@pytest.mark.skipif(not _find_brd_file(), reason="No BRD .docx in project uploads/jobs")
def test_job_create_and_analyze_documents_with_brd(client_with_business):
    """Create a job with a BRD file from uploads, call analyze-documents, assert 200 and response shape."""
    client, headers, business = client_with_business
    brd_path = _find_brd_file()
    assert brd_path is not None

    # Create job with the BRD file
    with open(brd_path, "rb") as f:
        file_bytes = f.read()
    create_resp = client.post(
        "/api/jobs",
        data={"title": "BRD test job", "description": "Test analyze with BRD"},
        files=[("files", (brd_path.name, file_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    job_data = create_resp.json()
    job_id = job_data["id"]
    assert job_data.get("files") and len(job_data["files"]) >= 1

    # Analyze documents (no hired agent -> extraction-only path)
    analyze_resp = client.post(
        f"/api/jobs/{job_id}/analyze-documents",
        headers=headers,
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    body = analyze_resp.json()
    assert "conversation" in body
    # Should have analysis and/or solutions/recommendations/next_steps
    has_content = (
        body.get("analysis")
        or body.get("solutions")
        or body.get("recommendations")
        or body.get("next_steps")
    )
    assert has_content, f"Expected analysis or solutions in response: {body}"

    # Optional: workflow hint may be present
    conv = body["conversation"]
    completion_items = [c for c in conv if c.get("type") == "completion"]
    if completion_items:
        last = completion_items[-1]
        assert "solutions" in last or "recommendations" in last or "next_steps" in last
