"""
Integration tests for Jobs + Workflow builder endpoints.

Uses in-memory SQLite and TestClient (via integration_client fixture).
Mocks external analysis/execution so no real network calls.
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _auth_headers(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


class TestJobsWorkflow:
    def test_workflow_preview_empty(self, integration_client: TestClient, business_user):
        # Use API create to ensure route path is covered
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Preview Job", "description": "D"},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        r2 = integration_client.get(
            f"/api/jobs/{job_id}/workflow/preview",
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 200
        body = r2.json()
        assert "steps" in body
        assert "total_cost" in body

    def test_create_job_rejects_bad_zip(self, integration_client: TestClient, business_user):
        bad = io.BytesIO(b"not-zip")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Zip Job"},
            files={"files": ("bad.zip", bad, "application/zip")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 400

    def test_create_job_accepts_zip_and_extracts_allowed_files(self, integration_client: TestClient, business_user):
        # Build a zip in-memory with one allowed and one blocked extension
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ok.txt", "hello")
            zf.writestr("bad.exe", "nope")
        buf.seek(0)

        r = integration_client.post(
            "/api/jobs",
            data={"title": "Zip Extract Job"},
            files={"files": ("bundle.zip", buf, "application/zip")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        job = r.json()
        # Only ok.txt should appear
        assert job["files"] is not None
        assert all(f["name"].endswith(".txt") for f in job["files"])

    def test_analyze_documents_adds_questions(self, integration_client: TestClient, business_user):
        # Create job without files first
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Analyze Job"},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        # Analyze without files → 400
        r2 = integration_client.post(
            f"/api/jobs/{job_id}/analyze-documents",
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 400

        # Now update job by uploading a txt file
        f = io.BytesIO(b"Requirements here")
        r3 = integration_client.put(
            f"/api/jobs/{job_id}",
            data={"title": "Analyze Job"},
            files={"files": ("req.txt", f, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r3.status_code == 200, r3.text

        # Mock analyzer so we don't call external endpoints
        with patch(
            "services.document_analyzer.DocumentAnalyzer.analyze_documents_and_generate_questions",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = {
                "analysis": "A",
                "questions": ["Q1?"],
                "recommendations": [],
                "solutions": [],
                "next_steps": [],
            }
            r4 = integration_client.post(
                f"/api/jobs/{job_id}/analyze-documents",
                headers=_auth_headers(business_user),
            )
        assert r4.status_code == 200, r4.text
        body = r4.json()
        assert body["analysis"] == "A"
        assert body["questions"] == ["Q1?"]
        assert any(item.get("type") == "question" for item in body["conversation"])

    def test_answer_question_flow(self, integration_client: TestClient, business_user):
        # Create job with file
        f = io.BytesIO(b"Req")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Q Job"},
            files={"files": ("req.txt", f, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        # Mock analyze to insert question
        with patch(
            "services.document_analyzer.DocumentAnalyzer.analyze_documents_and_generate_questions",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = {
                "analysis": "A",
                "questions": ["Q1?"],
                "recommendations": [],
                "solutions": [],
                "next_steps": [],
            }
            r2 = integration_client.post(
                f"/api/jobs/{job_id}/analyze-documents",
                headers=_auth_headers(business_user),
            )
        assert r2.status_code == 200

        # Mock processing user response
        with patch(
            "services.document_analyzer.DocumentAnalyzer.process_user_response",
            new_callable=AsyncMock,
        ) as mock2:
            mock2.return_value = {
                "analysis": "A2",
                "questions": [],
                "recommendations": ["R"],
                "solutions": ["S"],
                "next_steps": ["N"],
            }
            r3 = integration_client.post(
                f"/api/jobs/{job_id}/answer-question",
                json={"answer": "Ans"},
                headers=_auth_headers(business_user),
            )
        assert r3.status_code == 200, r3.text
        out = r3.json()
        assert out["analysis"] == "A2"

    def test_auto_split_and_update_step_tools(self, integration_client: TestClient, business_user, sample_agent):
        r = integration_client.post(
            "/api/jobs",
            data={"title": "WF Job"},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        r2 = integration_client.post(
            f"/api/jobs/{job_id}/workflow/auto-split",
            json={"agent_ids": [sample_agent.id], "tool_visibility": "names_only"},
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 200, r2.text
        preview = r2.json()
        assert "steps" in preview and len(preview["steps"]) >= 1

        r3 = integration_client.get(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert r3.status_code == 200
        steps = r3.json()["workflow_steps"]
        assert steps
        step_id = steps[0]["id"]

        r4 = integration_client.patch(
            f"/api/jobs/{job_id}/workflow/steps/{step_id}",
            json={"tool_visibility": "none"},
            headers=_auth_headers(business_user),
        )
        assert r4.status_code == 200, r4.text
        assert r4.json().get("tool_visibility") == "none"

