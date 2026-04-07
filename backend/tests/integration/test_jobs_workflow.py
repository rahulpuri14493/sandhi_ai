"""
Integration tests for Jobs + Workflow builder endpoints.

Uses in-memory SQLite and TestClient (via integration_client fixture).
Mocks external analysis/execution so no real network calls.
"""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from models.agent import Agent, AgentStatus


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

    def test_create_job_zip_sanitizes_nested_entry_paths(self, integration_client: TestClient, business_user):
        # Nested paths should be flattened to safe base names in persisted metadata.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("nested/folder/spec.txt", "hello")
            zf.writestr("../escape/unsafe.csv", "a,b\n1,2")
        buf.seek(0)

        r = integration_client.post(
            "/api/jobs",
            data={"title": "Zip Path Sanitization Job"},
            files={"files": ("bundle.zip", buf, "application/zip")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        files = r.json().get("files") or []
        names = sorted([f["name"] for f in files])
        assert names == ["spec.txt", "unsafe.csv"]
        assert all("/" not in n and "\\" not in n for n in names)

    def test_create_job_with_empty_zip_creates_job_without_documents(self, integration_client: TestClient, business_user):
        # Empty ZIP should create a job but no extracted files/documents.
        empty_zip = io.BytesIO()
        with zipfile.ZipFile(empty_zip, "w"):
            pass
        empty_zip.seek(0)

        create_resp = integration_client.post(
            "/api/jobs",
            data={"title": "Empty Zip Job"},
            files={"files": ("empty.zip", empty_zip, "application/zip")},
            headers=_auth_headers(business_user),
        )
        assert create_resp.status_code == 201, create_resp.text
        body = create_resp.json()
        assert body.get("files") is None
        job_id = body["id"]

        # Since no docs were extracted, analysis should fail with "no docs" validation.
        analyze_resp = integration_client.post(
            f"/api/jobs/{job_id}/analyze-documents",
            headers=_auth_headers(business_user),
        )
        assert analyze_resp.status_code == 400

    def test_create_job_zip_retries_on_transient_extract_error(self, integration_client: TestClient, business_user, monkeypatch):
        from core.config import settings
        import api.routes.jobs as jobs_routes

        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 2)
        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_JITTER_SECONDS", 0.0)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ok.txt", "hello")
        buf.seek(0)

        real_zip = zipfile.ZipFile
        state = {"calls": 0}

        def flaky_zip(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise OSError("transient zip read error")
            return real_zip(*args, **kwargs)

        with patch.object(jobs_routes.zipfile, "ZipFile", side_effect=flaky_zip):
            r = integration_client.post(
                "/api/jobs",
                data={"title": "Zip Retry Job"},
                files={"files": ("bundle.zip", buf, "application/zip")},
                headers=_auth_headers(business_user),
            )
        assert r.status_code == 201, r.text
        assert state["calls"] == 2

    def test_create_job_zip_fails_after_retry_exhaustion(self, integration_client: TestClient, business_user, monkeypatch):
        from core.config import settings
        import api.routes.jobs as jobs_routes

        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 2)
        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ZIP_EXTRACT_RETRY_JITTER_SECONDS", 0.0)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ok.txt", "hello")
        buf.seek(0)

        with patch.object(jobs_routes.zipfile, "ZipFile", side_effect=OSError("persistent zip read error")):
            r = integration_client.post(
                "/api/jobs",
                data={"title": "Zip Retry Exhausted Job"},
                files={"files": ("bundle.zip", buf, "application/zip")},
                headers=_auth_headers(business_user),
            )
        assert r.status_code == 500
        assert "after 2 attempts" in r.text

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

    def test_update_job_overwrites_existing_documents(self, integration_client: TestClient, business_user):
        # Create job with initial file
        first = io.BytesIO(b"old requirements")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Overwrite BRD Job"},
            files={"files": ("old.txt", first, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        assert r.json()["files"] and len(r.json()["files"]) == 1
        old_file_id = r.json()["files"][0]["id"]

        # Upload new file via update -> should replace, not append
        second = io.BytesIO(b"new requirements")
        r2 = integration_client.put(
            f"/api/jobs/{job_id}",
            files={"files": ("new.txt", second, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 200, r2.text
        files = r2.json().get("files") or []
        assert len(files) == 1
        assert files[0]["name"] == "new.txt"

        # Old file should no longer be downloadable
        r3 = integration_client.get(
            f"/api/jobs/{job_id}/files/{old_file_id}",
            headers=_auth_headers(business_user),
        )
        assert r3.status_code == 404

    def test_update_job_overwrites_zip_contents_instead_of_merging(self, integration_client: TestClient, business_user):
        # Create job with zip containing old txt
        old_zip = io.BytesIO()
        with zipfile.ZipFile(old_zip, "w") as zf:
            zf.writestr("old_a.txt", "old")
        old_zip.seek(0)
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Overwrite Zip Job"},
            files={"files": ("old_bundle.zip", old_zip, "application/zip")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        assert len(r.json().get("files") or []) == 1

        # Update with a different zip
        new_zip = io.BytesIO()
        with zipfile.ZipFile(new_zip, "w") as zf:
            zf.writestr("new_a.txt", "new")
            zf.writestr("new_b.txt", "new")
        new_zip.seek(0)
        r2 = integration_client.put(
            f"/api/jobs/{job_id}",
            files={"files": ("new_bundle.zip", new_zip, "application/zip")},
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 200, r2.text
        files = r2.json().get("files") or []
        names = sorted([f["name"] for f in files])
        assert names == ["new_a.txt", "new_b.txt"]

    def test_download_uploaded_file(self, integration_client: TestClient, business_user):
        """GET /api/jobs/{id}/files/{file_id} returns the file content for a valid upload."""
        payload = io.BytesIO(b"downloadable content")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Download Test"},
            files={"files": ("dl.txt", payload, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        file_id = r.json()["files"][0]["id"]

        r2 = integration_client.get(
            f"/api/jobs/{job_id}/files/{file_id}",
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 200
        assert r2.content == b"downloadable content"

    def test_delete_job_removes_files(self, integration_client: TestClient, business_user):
        """DELETE /api/jobs/{id} should succeed and remove associated files from disk."""

        payload = io.BytesIO(b"to-be-deleted")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Delete Cleanup"},
            files={"files": ("del.txt", payload, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        # Fetch internal file path via get
        r2 = integration_client.get(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert r2.status_code == 200

        # Delete the job
        r3 = integration_client.delete(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert r3.status_code in (200, 204)

        # Verify the job is gone
        r4 = integration_client.get(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert r4.status_code == 404

    def test_file_metadata_redacted_in_response(self, integration_client: TestClient, business_user):
        """API responses should never expose internal storage fields (path, bucket, key, storage)."""
        payload = io.BytesIO(b"secret path")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Redaction Test"},
            files={"files": ("r.txt", payload, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 201, r.text
        for f in r.json().get("files") or []:
            assert "path" not in f
            assert "bucket" not in f
            assert "key" not in f
            assert "storage" not in f

    def test_create_job_rejects_oversized_file(self, integration_client: TestClient, business_user, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "JOB_UPLOAD_MAX_FILE_BYTES", 8)
        payload = io.BytesIO(b"this-is-too-large")
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Large file test"},
            files={"files": ("large.txt", payload, "text/plain")},
            headers=_auth_headers(business_user),
        )
        assert r.status_code == 413, r.text

    def test_auto_split_strict_scope_with_external_brd_zip(
        self,
        integration_client: TestClient,
        integration_db_session,
        business_user,
        developer_user,
    ):
        """
        Strict integration test using external d:\\BRD.zip:
        - Upload ZIP and verify extracted BRDs are present
        - Auto-split with mapping in job description:
          anova_test -> OpenAI agent, chi_square_test -> Groq agent
        - Assert each workflow step receives only its assigned BRD scope
        """
        zip_path = Path(__file__).resolve().parents[1] / "fixtures" / "BRD.zip"
        if not zip_path.exists():
            pytest.skip("Requires test fixture: backend/tests/fixtures/BRD.zip")

        # Create two active hired agents (OpenAI/Groq) for deterministic step order.
        dev = developer_user["user"]
        openai_agent = Agent(
            developer_id=dev.id,
            name="OpenAI Agent",
            description="Specialized in anova_test BRD requirements",
            status=AgentStatus.ACTIVE,
            price_per_task=10.0,
            price_per_communication=1.0,
            api_endpoint="https://example.com/v1/chat/completions",
            api_key="sk-openai-test",
            llm_model="gpt-4o-mini",
            a2a_enabled=False,
        )
        groq_agent = Agent(
            developer_id=dev.id,
            name="Groq Agent",
            description="Specialized in chi_square_test BRD requirements",
            status=AgentStatus.ACTIVE,
            price_per_task=10.0,
            price_per_communication=1.0,
            api_endpoint="https://example.com/v1/chat/completions",
            api_key="sk-groq-test",
            llm_model="gpt-4o-mini",
            a2a_enabled=False,
        )
        integration_db_session.add(openai_agent)
        integration_db_session.add(groq_agent)
        integration_db_session.commit()
        integration_db_session.refresh(openai_agent)
        integration_db_session.refresh(groq_agent)

        description = (
            "The multi agents are specialized in solving arithmetic problems. "
            "Attach the zip file. "
            "anova_test document handled by OpenAI Agent. "
            "chi_square_test document handled by Groq Agent. "
            "Give the output only and do not provide approach explanation."
        )

        with zip_path.open("rb") as f:
            create_resp = integration_client.post(
                "/api/jobs",
                data={"title": "Strict BRD Agent Mapping", "description": description},
                files={"files": ("BRD.zip", f.read(), "application/zip")},
                headers=_auth_headers(business_user),
            )
        assert create_resp.status_code == 201, create_resp.text
        job_payload = create_resp.json()
        job_id = job_payload["id"]
        files = job_payload.get("files") or []
        assert len(files) >= 2, "Expected at least two BRDs extracted from BRD.zip"

        anova_meta = next((x for x in files if "anova_test" in (x.get("name", "").lower())), None)
        chi_meta = next((x for x in files if "chi_square_test" in (x.get("name", "").lower())), None)
        assert anova_meta is not None, "Expected anova_test BRD file in extracted ZIP content"
        assert chi_meta is not None, "Expected chi_square_test BRD file in extracted ZIP content"

        # Mock splitter API response without assigned_document_ids so explicit mapping
        # in description is used by strict scoping logic.
        llm_assignments = [
            {"agent_index": 0, "task": "Handle ANOVA calculations only."},
            {"agent_index": 1, "task": "Handle chi-square calculations only."},
        ]
        with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
            with patch(
                "services.task_splitter.planner_chat_completion",
                new=AsyncMock(return_value=json.dumps(llm_assignments)),
            ) as mock_planner:
                split_resp = integration_client.post(
                    f"/api/jobs/{job_id}/workflow/auto-split",
                    json={"agent_ids": [openai_agent.id, groq_agent.id], "workflow_mode": "independent"},
                    headers=_auth_headers(business_user),
                )
        assert split_resp.status_code == 200, split_resp.text
        mock_planner.assert_awaited()

        job_resp = integration_client.get(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert job_resp.status_code == 200, job_resp.text
        workflow_steps = sorted(job_resp.json().get("workflow_steps") or [], key=lambda s: s["step_order"])
        assert len(workflow_steps) == 2

        step1_input = json.loads(workflow_steps[0]["input_data"])
        step2_input = json.loads(workflow_steps[1]["input_data"])

        # Strict document scope must be enabled on both steps.
        assert step1_input.get("document_scope_restricted") is True
        assert step2_input.get("document_scope_restricted") is True

        step1_docs = step1_input.get("documents") or []
        step2_docs = step2_input.get("documents") or []
        assert len(step1_docs) == 1
        assert len(step2_docs) == 1

        step1_doc_name = (step1_docs[0].get("name") or "").lower()
        step2_doc_name = (step2_docs[0].get("name") or "").lower()
        assert "anova_test" in step1_doc_name
        assert "chi_square_test" in step2_doc_name
        assert "chi_square_test" not in step1_doc_name
        assert "anova_test" not in step2_doc_name

    def test_auto_split_single_agent_zip_keeps_all_brd_scope_without_network_splitter_call(
        self,
        integration_client: TestClient,
        integration_db_session,
        business_user,
        developer_user,
    ):
        """
        Complex single-agent scenario using BRD.zip:
        - uploads multi-BRD zip
        - auto-split with exactly one agent
        - no outbound splitter LLM call is made
        - single step keeps full BRD scope (all extracted docs)
        """
        zip_path = Path(__file__).resolve().parents[1] / "fixtures" / "BRD.zip"
        if not zip_path.exists():
            pytest.skip("Requires test fixture: backend/tests/fixtures/BRD.zip")

        dev = developer_user["user"]
        solo_agent = Agent(
            developer_id=dev.id,
            name="Solo OpenAI Agent",
            description="Handles full BRD workload end-to-end",
            status=AgentStatus.ACTIVE,
            price_per_task=10.0,
            price_per_communication=1.0,
            api_endpoint="https://example.com/v1/chat/completions",
            api_key="sk-solo-test",
            llm_model="gpt-4o-mini",
            a2a_enabled=False,
        )
        integration_db_session.add(solo_agent)
        integration_db_session.commit()
        integration_db_session.refresh(solo_agent)

        with zip_path.open("rb") as f:
            create_resp = integration_client.post(
                "/api/jobs",
                data={"title": "Single Agent BRD Zip", "description": "One agent should handle all BRDs in this zip."},
                files={"files": ("BRD.zip", f.read(), "application/zip")},
                headers=_auth_headers(business_user),
            )
        assert create_resp.status_code == 201, create_resp.text
        job_id = create_resp.json()["id"]

        with patch("services.task_splitter.planner_chat_completion", new=AsyncMock()) as mock_planner:
            split_resp = integration_client.post(
                f"/api/jobs/{job_id}/workflow/auto-split",
                json={"agent_ids": [solo_agent.id], "workflow_mode": "independent"},
                headers=_auth_headers(business_user),
            )
        assert split_resp.status_code == 200, split_resp.text
        assert mock_planner.await_count == 0

        job_resp = integration_client.get(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert job_resp.status_code == 200, job_resp.text
        workflow_steps = sorted(job_resp.json().get("workflow_steps") or [], key=lambda s: s["step_order"])
        assert len(workflow_steps) == 1
        step_input = json.loads(workflow_steps[0]["input_data"])
        assert step_input.get("document_scope_restricted") is True
        step_docs = step_input.get("documents") or []
        doc_names = [(d.get("name") or "").lower() for d in step_docs]
        assert any("anova_test" in n for n in doc_names)
        assert any("chi_square_test" in n for n in doc_names)
        allowed_ids = step_input.get("allowed_document_ids") or []
        assert len(allowed_ids) == len(step_docs)

    def test_auto_split_multi_agent_uses_mocked_llm_document_ids_from_zip(
        self,
        integration_client: TestClient,
        integration_db_session,
        business_user,
        developer_user,
    ):
        """
        Complex multi-agent scenario where mocked splitter explicitly returns assigned_document_ids.
        Ensures strict per-agent BRD filtering is enforced based on mocked AI output.
        """
        zip_path = Path(__file__).resolve().parents[1] / "fixtures" / "BRD.zip"
        if not zip_path.exists():
            pytest.skip("Requires test fixture: backend/tests/fixtures/BRD.zip")

        dev = developer_user["user"]
        openai_agent = Agent(
            developer_id=dev.id,
            name="OpenAI Agent",
            description="Math and ANOVA specialist",
            status=AgentStatus.ACTIVE,
            price_per_task=10.0,
            price_per_communication=1.0,
            api_endpoint="https://example.com/v1/chat/completions",
            api_key="sk-openai-test",
            llm_model="gpt-4o-mini",
            a2a_enabled=False,
        )
        groq_agent = Agent(
            developer_id=dev.id,
            name="Groq Agent",
            description="Chi-square specialist",
            status=AgentStatus.ACTIVE,
            price_per_task=10.0,
            price_per_communication=1.0,
            api_endpoint="https://example.com/v1/chat/completions",
            api_key="sk-groq-test",
            llm_model="gpt-4o-mini",
            a2a_enabled=False,
        )
        integration_db_session.add(openai_agent)
        integration_db_session.add(groq_agent)
        integration_db_session.commit()
        integration_db_session.refresh(openai_agent)
        integration_db_session.refresh(groq_agent)

        with zip_path.open("rb") as f:
            create_resp = integration_client.post(
                "/api/jobs",
                data={
                    "title": "Mocked AI BRD Assignment",
                    "description": "Split the two BRDs between two AI agents and return concise outputs.",
                },
                files={"files": ("BRD.zip", f.read(), "application/zip")},
                headers=_auth_headers(business_user),
            )
        assert create_resp.status_code == 201, create_resp.text
        job_payload = create_resp.json()
        job_id = job_payload["id"]
        files = job_payload.get("files") or []
        anova_meta = next((x for x in files if "anova_test" in (x.get("name", "").lower())), None)
        chi_meta = next((x for x in files if "chi_square_test" in (x.get("name", "").lower())), None)
        assert anova_meta is not None
        assert chi_meta is not None

        llm_assignments = [
            {
                "agent_index": 0,
                "task": "Handle only ANOVA BRD and produce final ANOVA answer only.",
                "assigned_document_ids": [anova_meta["id"]],
            },
            {
                "agent_index": 1,
                "task": "Handle only chi-square BRD and produce final chi-square answer only.",
                "assigned_document_ids": [chi_meta["id"]],
            },
        ]
        with patch("services.task_splitter.is_agent_planner_configured", return_value=True):
            with patch(
                "services.task_splitter.planner_chat_completion",
                new=AsyncMock(return_value=json.dumps(llm_assignments)),
            ) as mock_planner:
                split_resp = integration_client.post(
                    f"/api/jobs/{job_id}/workflow/auto-split",
                    json={"agent_ids": [openai_agent.id, groq_agent.id], "workflow_mode": "independent"},
                    headers=_auth_headers(business_user),
                )
        assert split_resp.status_code == 200, split_resp.text
        mock_planner.assert_awaited()

        job_resp = integration_client.get(
            f"/api/jobs/{job_id}",
            headers=_auth_headers(business_user),
        )
        assert job_resp.status_code == 200, job_resp.text
        workflow_steps = sorted(job_resp.json().get("workflow_steps") or [], key=lambda s: s["step_order"])
        assert len(workflow_steps) == 2

        step1_input = json.loads(workflow_steps[0]["input_data"])
        step2_input = json.loads(workflow_steps[1]["input_data"])
        assert step1_input.get("document_scope_restricted") is True
        assert step2_input.get("document_scope_restricted") is True
        assert step1_input.get("allowed_document_ids") == [anova_meta["id"]]
        assert step2_input.get("allowed_document_ids") == [chi_meta["id"]]
        assert len(step1_input.get("documents") or []) == 1
        assert len(step2_input.get("documents") or []) == 1
        assert (step1_input["documents"][0].get("name") or "").lower().find("anova_test") >= 0
        assert (step2_input["documents"][0].get("name") or "").lower().find("chi_square_test") >= 0

