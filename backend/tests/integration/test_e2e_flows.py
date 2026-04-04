"""
End-to-end integration tests for all main API features.

Uses in-memory SQLite and TestClient. Mocks external HTTP (document analyzer, agent executor)
where needed so tests don't call real endpoints.
"""
import json
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient



# ---------- Auth ----------
class TestE2EAuth:
    """Auth: register, login, /me."""

    def test_register_and_login_and_me(self, integration_client: TestClient):
        email = f"e2e-{uuid.uuid4().hex[:8]}@test.com"
        # Register
        r = integration_client.post(
            "/api/auth/register",
            json={"email": email, "password": "secret123", "role": "business"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["email"] == email
        assert data["role"] == "business"
        # Login
        r2 = integration_client.post(
            "/api/auth/login",
            json={"email": email, "password": "secret123"},
        )
        assert r2.status_code == 200
        token = r2.json()["access_token"]
        # Me
        r3 = integration_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r3.status_code == 200
        assert r3.json()["email"] == email


# ---------- Agents ----------
class TestE2EAgents:
    """Agents: list, get, create, update, test-connection, reviews, a2a-card."""

    def test_list_and_get_agent(
        self, integration_client: TestClient, developer_user, sample_agent
    ):
        token = developer_user["token"]
        headers = {"Authorization": f"Bearer {token}"}
        r = integration_client.get("/api/agents", headers=headers)
        assert r.status_code == 200
        agents = r.json()
        assert isinstance(agents, list)
        assert any(a["id"] == sample_agent.id for a in agents)
        r2 = integration_client.get(f"/api/agents/{sample_agent.id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == sample_agent.name

    def test_create_agent(self, integration_client: TestClient, developer_user):
        token = developer_user["token"]
        r = integration_client.post(
            "/api/agents",
            json={
                "name": "New E2E Agent",
                "description": "Created in e2e",
                "price_per_task": 5.0,
                "price_per_communication": 0.5,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "New E2E Agent"

    def test_create_agent_as_developer_publish_flow(self, integration_client: TestClient):
        """
        E2E: developer (publisher) registers, logs in, creates an agent, then loads it by id.
        Payload matches what the publish UI sends: no client-supplied status (AgentCreate forbids it).
        """
        email = f"publisher-e2e-{uuid.uuid4().hex[:8]}@test.com"
        password = "Publish1a"  # letters + digits, min 8 chars

        reg = integration_client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "role": "developer"},
        )
        assert reg.status_code == 201, reg.text
        publisher = reg.json()
        assert publisher["role"] == "developer"
        publisher_id = publisher["id"]

        login = integration_client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        payload = {
            "name": "E2E Published Agent",
            "description": "Created via full developer publish flow",
            "capabilities": ["nlp", "automation"],
            "pricing_model": "pay_per_use",
            "price_per_task": 1.5,
            "price_per_communication": 0.25,
            "llm_model": "gpt-4o-mini",
            "temperature": 0.7,
            "api_endpoint": "https://api.example.com/v1/chat/completions",
            "a2a_enabled": False,
        }
        create = integration_client.post("/api/agents", json=payload, headers=headers)
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["name"] == payload["name"]
        assert body["developer_id"] == publisher_id
        assert body.get("status") == "pending"
        assert body.get("pricing_model") == "pay_per_use"
        assert body.get("capabilities") == ["nlp", "automation"]

        agent_id = body["id"]
        fetched = integration_client.get(f"/api/agents/{agent_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        again = fetched.json()
        assert again["id"] == agent_id
        assert again["name"] == payload["name"]
        assert again["developer_id"] == publisher_id

    def test_update_agent(
        self, integration_client: TestClient, developer_user, sample_agent
    ):
        token = developer_user["token"]
        r = integration_client.put(
            f"/api/agents/{sample_agent.id}",
            json={
                "name": "Updated E2E Agent",
                "description": sample_agent.description,
                "status": "active",
                "price_per_task": sample_agent.price_per_task,
                "price_per_communication": sample_agent.price_per_communication,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Updated E2E Agent"

    def test_test_connection(
        self, integration_client: TestClient, developer_user, sample_agent
    ):
        token = developer_user["token"]
        with patch("services.a2a_client.send_message", new_callable=AsyncMock) as mock:
            mock.return_value = {"content": "OK"}
            r = integration_client.post(
                "/api/agents/test-connection",
                json={
                    "api_endpoint": sample_agent.api_endpoint,
                    "api_key": sample_agent.api_key,
                    "a2a_enabled": False,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200
        body = r.json()
        assert "success" in body

    def test_agent_reviews(
        self, integration_client: TestClient, business_user, sample_agent
    ):
        token = business_user["token"]
        # Submit review (business can review)
        r = integration_client.post(
            f"/api/agents/{sample_agent.id}/reviews",
            json={"rating": 5, "review_text": "Great e2e"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        r2 = integration_client.get(
            f"/api/agents/{sample_agent.id}/reviews?limit=10&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200

    def test_agent_a2a_card(
        self, integration_client: TestClient, developer_user, sample_agent
    ):
        token = developer_user["token"]
        r = integration_client.get(
            f"/api/agents/{sample_agent.id}/a2a-card",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


# ---------- Jobs ----------
class TestE2EJobs:
    """Jobs: create (with file), get, analyze-documents (mocked), answer-question, workflow, approve, execute (mocked), share-link, status."""

    def test_create_job_with_file(
        self, integration_client: TestClient, business_user, temp_upload_file
    ):
        token = business_user["token"]
        with open(temp_upload_file, "rb") as f:
            content = f.read()
        r = integration_client.post(
            "/api/jobs",
            data={"title": "E2E Job", "description": "Integration test"},
            files=[("files", ("req.txt", content, "text/plain"))],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        job = r.json()
        assert job["title"] == "E2E Job"
        assert job.get("files") and len(job["files"]) >= 1

    def test_job_full_flow(
        self,
        integration_client: TestClient,
        integration_db_session,
        business_user,
        developer_user,
        sample_agent,
        temp_upload_file,
    ):
        """Create job -> analyze (mocked) -> answer-question -> workflow -> approve -> execute (mocked) -> share-link -> status."""
        token = business_user["token"]
        with open(temp_upload_file, "rb") as f:
            content = f.read()
        # Create job
        r = integration_client.post(
            "/api/jobs",
            data={"title": "E2E Full Flow", "description": "Test"},
            files=[("files", ("req.txt", content, "text/plain"))],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        # Analyze documents (mock analyzer)
        mock_result = {
            "analysis": "E2E analysis",
            "questions": [],
            "recommendations": ["Use agent"],
            "solutions": ["Solution 1"],
            "next_steps": ["Approve and run"],
        }
        with patch(
            "services.document_analyzer.DocumentAnalyzer.analyze_documents_and_generate_questions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            r2 = integration_client.post(
                f"/api/jobs/{job_id}/analyze-documents",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r2.status_code == 200
        conv = r2.json().get("conversation", [])
        assert any(item.get("type") == "analysis" for item in conv)

        # Workflow: manual steps with our agent (skip answer-question since mock had no questions) (body is list of step dicts)
        r4 = integration_client.post(
            f"/api/jobs/{job_id}/workflow/manual",
            json=[{"agent_id": sample_agent.id, "step_order": 1}],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r4.status_code == 200

        # Preview
        r5 = integration_client.get(
            f"/api/jobs/{job_id}/workflow/preview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r5.status_code == 200

        # Approve
        r6 = integration_client.post(
            f"/api/jobs/{job_id}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r6.status_code == 200

        # Execute (mock executor so no real HTTP)
        with patch(
            "services.agent_executor.AgentExecutor._execute_agent",
            new_callable=AsyncMock,
            return_value={"content": "E2E step output"},
        ):
            r7 = integration_client.post(
                f"/api/jobs/{job_id}/execute",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r7.status_code == 200
        # Execute runs in background; response may show in_progress until task finishes
        assert r7.json().get("status") in ("completed", "in_progress")

        # Share link
        r8 = integration_client.get(
            f"/api/jobs/{job_id}/share-link",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r8.status_code == 200
        assert "share_url" in r8.json() or "token" in r8.json()

        # Status
        r9 = integration_client.get(
            f"/api/jobs/{job_id}/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r9.status_code == 200

    def test_planner_status_and_artifacts_list_for_job(
        self, integration_client: TestClient, business_user
    ):
        """E2E: authenticated planner/status and empty planner-artifacts for a new job."""
        token = business_user["token"]
        headers = {"Authorization": f"Bearer {token}"}

        r0 = integration_client.get("/api/jobs/planner/status", headers=headers)
        assert r0.status_code == 200
        st = r0.json()
        assert "configured" in st
        assert "provider" in st

        r = integration_client.post(
            "/api/jobs",
            data={"title": "Planner audit job", "description": "e2e"},
            headers=headers,
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        r2 = integration_client.get(f"/api/jobs/{job_id}/planner-artifacts", headers=headers)
        assert r2.status_code == 200
        assert r2.json().get("items") == []

        r3 = integration_client.get(f"/api/jobs/{job_id}/planner-pipeline", headers=headers)
        assert r3.status_code == 200
        pipe = r3.json()
        assert pipe["schema_version"] == "planner_pipeline.v1"
        assert pipe["job_id"] == job_id
        assert pipe["brd_analysis"] is None
        assert pipe["task_split"] is None
        assert pipe["tool_suggestion"] is None

    def test_planner_pipeline_with_stored_artifact(
        self, integration_client: TestClient, business_user, integration_db_session, tmp_path
    ):
        """E2E: create job via API, attach local planner file row, read composed planner-pipeline."""
        from models.job import JobPlannerArtifact

        token = business_user["token"]
        headers = {"Authorization": f"Bearer {token}"}
        r = integration_client.post(
            "/api/jobs",
            data={"title": "Planner pipeline e2e", "description": "with artifact"},
            headers=headers,
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        jf = tmp_path / "e2e_planner_pipeline.json"
        jf.write_text('{"e2e": true, "stage": "brd"}', encoding="utf-8")
        integration_db_session.add(
            JobPlannerArtifact(
                job_id=job_id,
                artifact_type="brd_analysis",
                storage="local",
                bucket=None,
                object_key=str(jf),
                byte_size=int(jf.stat().st_size),
            )
        )
        integration_db_session.commit()

        rp = integration_client.get(f"/api/jobs/{job_id}/planner-pipeline", headers=headers)
        assert rp.status_code == 200, rp.text
        body = rp.json()
        assert body["schema_version"] == "planner_pipeline.v1"
        assert body["job_id"] == job_id
        assert body["brd_analysis"] == {"e2e": True, "stage": "brd"}
        assert body["task_split"] is None
        assert body["tool_suggestion"] is None
        assert body["artifact_ids"]["brd_analysis"] is not None


# ---------- Dashboards ----------
class TestE2EDashboards:
    """Dashboards: developer earnings/agents/stats, business jobs/spending."""

    def test_developer_dashboard(
        self, integration_client: TestClient, developer_user, sample_agent
    ):
        token = developer_user["token"]
        for path in ["/api/developers/earnings", "/api/developers/agents", "/api/developers/stats"]:
            r = integration_client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200

    def test_business_dashboard(
        self, integration_client: TestClient, business_user
    ):
        token = business_user["token"]
        for path in ["/api/businesses/jobs", "/api/businesses/spending"]:
            r = integration_client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200


# ---------- Hiring ----------
class TestE2EHiring:
    """Hiring: create position, list, get."""

    def test_hiring_positions(
        self, integration_client: TestClient, business_user, developer_user
    ):
        token_b = business_user["token"]
        token_d = developer_user["token"]
        # Business creates position
        r = integration_client.post(
            "/api/hiring/positions",
            json={"title": "E2E Position", "description": "Test role"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r.status_code == 201
        pos = r.json()
        pos_id = pos["id"]
        # List (any auth)
        r2 = integration_client.get(
            "/api/hiring/positions",
            headers={"Authorization": f"Bearer {token_d}"},
        )
        assert r2.status_code == 200
        assert any(p["id"] == pos_id for p in r2.json())
        # Get one
        r3 = integration_client.get(
            f"/api/hiring/positions/{pos_id}",
            headers={"Authorization": f"Bearer {token_d}"},
        )
        assert r3.status_code == 200
        assert r3.json()["title"] == "E2E Position"


# ---------- Payments ----------
class TestE2EPayments:
    """Payments: calculate, list transactions."""

    def test_payments_calculate_and_transactions(
        self,
        integration_client: TestClient,
        business_user,
        integration_db_session,
        sample_agent,
    ):
        from models.job import Job, JobStatus, WorkflowStep

        token = business_user["token"]
        business = business_user["user"]
        # Create a job with workflow so cost exists
        job = Job(
            business_id=business.id,
            title="Pay E2E",
            status=JobStatus.DRAFT,
            files=json.dumps([]),
            conversation=json.dumps([]),
        )
        integration_db_session.add(job)
        integration_db_session.commit()
        integration_db_session.refresh(job)
        step = WorkflowStep(
            job_id=job.id,
            agent_id=sample_agent.id,
            step_order=1,
            status="pending",
        )
        integration_db_session.add(step)
        integration_db_session.commit()
        r = integration_client.post(
            "/api/payments/calculate",
            params={"job_id": job.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        r2 = integration_client.get(
            "/api/payments/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200
        assert isinstance(r2.json(), list)


# ---------- External jobs (share link) ----------
class TestE2EExternalJobs:
    """External: share-link then get job by token."""

    def test_share_link_and_get_by_token(
        self,
        integration_client: TestClient,
        business_user,
        integration_db_session,
        sample_agent,
    ):
        from core.external_token import create_job_token
        from models.job import Job, JobStatus

        business_user["token"]
        business = business_user["user"]
        job = Job(
            business_id=business.id,
            title="External E2E",
            status=JobStatus.COMPLETED,
            files=json.dumps([]),
            conversation=json.dumps([]),
        )
        integration_db_session.add(job)
        integration_db_session.commit()
        integration_db_session.refresh(job)
        share_token = create_job_token(job.id)
        r = integration_client.get(
            f"/api/external/jobs/{job.id}",
            params={"token": share_token},
        )
        assert r.status_code == 200
        assert r.json().get("title") == "External E2E"
        r2 = integration_client.get(
            f"/api/external/jobs/{job.id}/status",
            params={"token": share_token},
        )
        assert r2.status_code == 200
