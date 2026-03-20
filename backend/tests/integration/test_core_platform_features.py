"""
Core platform feature coverage tests.

These tests enforce coverage for the platform's critical paths:
- auth
- agent discovery
- job lifecycle (create -> analyze -> workflow -> approve -> execute -> status)
- MCP registry/tool validation
- payments preview
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from models.job import Job, JobStatus, WorkflowStep


def _auth_headers(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def test_core_health_endpoint(integration_client: TestClient):
    r = integration_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") in ("ok", "healthy")


def test_core_auth_register_login_me(integration_client: TestClient):
    email = f"core-{uuid.uuid4().hex[:8]}@test.com"
    r = integration_client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "role": "business"},
    )
    assert r.status_code == 201

    r2 = integration_client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123"},
    )
    assert r2.status_code == 200
    token = r2.json()["access_token"]

    r3 = integration_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r3.status_code == 200
    assert r3.json()["email"] == email


def test_core_agent_list_and_get(integration_client: TestClient, developer_user, sample_agent):
    headers = _auth_headers(developer_user)
    r = integration_client.get("/api/agents", headers=headers)
    assert r.status_code == 200
    assert any(a["id"] == sample_agent.id for a in r.json())

    r2 = integration_client.get(f"/api/agents/{sample_agent.id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["id"] == sample_agent.id


def test_core_job_lifecycle(
    integration_client: TestClient,
    integration_db_session,
    business_user,
    sample_agent,
    temp_upload_file,
):
    headers = _auth_headers(business_user)

    with open(temp_upload_file, "rb") as f:
        content = f.read()
    r = integration_client.post(
        "/api/jobs",
        data={"title": "Core Lifecycle", "description": "Critical path"},
        files=[("files", ("req.txt", content, "text/plain"))],
        headers=headers,
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    with patch(
        "services.document_analyzer.DocumentAnalyzer.analyze_documents_and_generate_questions",
        new_callable=AsyncMock,
    ) as mock_analyze:
        mock_analyze.return_value = {
            "analysis": "core analysis",
            "questions": [],
            "recommendations": [],
            "solutions": [],
            "next_steps": [],
        }
        r2 = integration_client.post(f"/api/jobs/{job_id}/analyze-documents", headers=headers)
    assert r2.status_code == 200, r2.text
    assert any(item.get("type") == "analysis" for item in (r2.json().get("conversation") or []))

    r3 = integration_client.post(
        f"/api/jobs/{job_id}/workflow/manual",
        json=[{"agent_id": sample_agent.id, "step_order": 1}],
        headers=headers,
    )
    assert r3.status_code == 200, r3.text

    r4 = integration_client.get(f"/api/jobs/{job_id}/workflow/preview", headers=headers)
    assert r4.status_code == 200, r4.text
    assert "total_cost" in r4.json()

    r5 = integration_client.post(f"/api/jobs/{job_id}/approve", headers=headers)
    assert r5.status_code == 200, r5.text

    with patch(
        "services.agent_executor.AgentExecutor._execute_agent",
        new_callable=AsyncMock,
        return_value={"content": "ok"},
    ):
        r6 = integration_client.post(f"/api/jobs/{job_id}/execute", headers=headers)
    assert r6.status_code == 200, r6.text
    assert r6.json().get("status") in ("in_progress", "completed")

    r7 = integration_client.get(f"/api/jobs/{job_id}/status", headers=headers)
    assert r7.status_code == 200


def test_core_mcp_registry_and_tool_validation(integration_client: TestClient, business_user):
    headers = _auth_headers(business_user)

    r = integration_client.post(
        "/api/mcp/tools/validate",
        json={"tool_type": "filesystem", "config": {"base_path": "."}},
        headers=headers,
    )
    assert r.status_code == 200
    assert "valid" in r.json()

    with patch("services.mcp_client.list_tools", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = {"tools": []}
        r2 = integration_client.get("/api/mcp/registry", headers=headers)
    assert r2.status_code == 200
    assert "platform_tools" in r2.json()


def test_core_payments_calculate(integration_client: TestClient, integration_db_session, business_user, sample_agent):
    business = business_user["user"]
    headers = _auth_headers(business_user)

    job = Job(
        business_id=business.id,
        title="Core Payment",
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
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "total_cost" in body
