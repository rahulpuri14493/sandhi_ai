"""
E2E integration coverage for MCP + job + AI agent + data output contracts.

Covers:
- Job execution with AI output persisted as artifact
- Platform write-mode invoking MCP write tool via artifact reference
- Agent write-mode skipping platform write invocation
"""

import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from models.agent import Agent, AgentStatus
from core.config import settings


def _auth_headers(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _create_job_with_output_contract(integration_client: TestClient, business_user, *, write_execution_mode: str):
    contract = {
        "version": "1.0",
        "write_targets": [
            {
                "tool_name": "platform_1_snowflake_kyc_results",
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": ["customer_id"],
                "target": {"database": "BANK", "schema": "RISK", "table": "KYC_AML_DECISIONS"},
            }
        ],
    }
    r = integration_client.post(
        "/api/jobs",
        data={
            "title": f"E2E output contract ({write_execution_mode})",
            "description": "Validate artifact-first MCP output execution",
            "write_execution_mode": write_execution_mode,
            "output_artifact_format": "jsonl",
            "output_contract": json.dumps(contract),
        },
        headers=_auth_headers(business_user),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_job_with_custom_output_contract(
    integration_client: TestClient, business_user, *, write_execution_mode: str, contract: dict
):
    r = integration_client.post(
        "/api/jobs",
        data={
            "title": f"E2E output contract custom ({write_execution_mode})",
            "description": "Validate artifact-first MCP output execution",
            "write_execution_mode": write_execution_mode,
            "output_artifact_format": "jsonl",
            "output_contract": json.dumps(contract),
        },
        headers=_auth_headers(business_user),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _prepare_single_step_workflow(integration_client: TestClient, business_user, job_id: int, sample_agent_id: int):
    r = integration_client.post(
        f"/api/jobs/{job_id}/workflow/manual",
        json=[{"agent_id": sample_agent_id, "step_order": 1}],
        headers=_auth_headers(business_user),
    )
    assert r.status_code == 200, r.text
    r2 = integration_client.post(f"/api/jobs/{job_id}/approve", headers=_auth_headers(business_user))
    assert r2.status_code == 200, r2.text


def test_e2e_platform_mode_persists_artifact_and_calls_mcp_write(
    integration_client: TestClient, integration_db_session, business_user, sample_agent, monkeypatch
):
    # This test asserts a filesystem artifact path; force local storage even when CI/Docker default is s3.
    monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")
    job_id = _create_job_with_output_contract(integration_client, business_user, write_execution_mode="platform")
    _prepare_single_step_workflow(integration_client, business_user, job_id, sample_agent.id)

    async def _agent_output(*args, **kwargs):
        return {
            "records": [
                {"customer_id": "C-001", "decision": "escalate", "confidence": 0.91},
                {"customer_id": "C-002", "decision": "nfa", "confidence": 0.74},
            ]
        }

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_agent_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock) as mock_call_tool:
        from services.agent_executor import AgentExecutor

        mock_call_tool.return_value = {"content": [{"type": "text", "text": "write-ok"}], "isError": False}
        mock_queue.return_value = None

        r = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert r.status_code == 200, r.text
        asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))

    status = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth_headers(business_user))
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] in ("completed", "in_progress")
    step = (body.get("workflow_steps") or [])[0]
    step_output = json.loads(step["output_data"])
    artifact_ref = step_output.get("artifact_ref") or {}
    assert artifact_ref.get("format") == "jsonl"
    assert step_output.get("write_execution_mode") == "platform"
    assert isinstance(step_output.get("write_results"), list)
    assert len(step_output["write_results"]) == 1
    assert mock_call_tool.await_count == 1

    # In test mode (local object storage), artifact path should exist and contain persisted records.
    artifact_path = artifact_ref.get("path")
    assert artifact_path, "Expected local artifact path to be recorded"
    p = Path(artifact_path)
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert "customer_id" in txt and "C-001" in txt


def test_e2e_agent_mode_persists_artifact_without_platform_write(
    integration_client: TestClient, integration_db_session, business_user, sample_agent
):
    job_id = _create_job_with_output_contract(integration_client, business_user, write_execution_mode="agent")
    _prepare_single_step_workflow(integration_client, business_user, job_id, sample_agent.id)

    async def _agent_output(*args, **kwargs):
        return {"records": [{"customer_id": "C-010", "decision": "nfa", "confidence": 0.88}]}

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_agent_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock) as mock_call_tool:
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None

        r = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert r.status_code == 200, r.text
        asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))

    status = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth_headers(business_user))
    assert status.status_code == 200, status.text
    step = (status.json().get("workflow_steps") or [])[0]
    step_output = json.loads(step["output_data"])
    artifact_ref = step_output.get("artifact_ref") or {}
    assert artifact_ref.get("format") == "jsonl"
    assert step_output.get("write_execution_mode") == "agent"
    assert step_output.get("write_results") == []
    assert mock_call_tool.await_count == 0


def test_e2e_platform_mode_multi_step_multi_target_write_fanout(
    integration_client: TestClient, integration_db_session, business_user, developer_user, sample_agent
):
    contract = {
        "version": "1.0",
        "write_targets": [
            {
                "tool_name": "platform_1_snowflake_kyc_results",
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": ["customer_id"],
                "target": {"database": "BANK", "schema": "RISK", "table": "KYC_AML_DECISIONS"},
            },
            {
                "tool_name": "platform_1_s3_audit_archive",
                "operation_type": "append",
                "write_mode": "append",
                "merge_keys": [],
                "target": {"bucket": "audit", "prefix": "jobs/kyc"},
            },
        ],
    }
    create = integration_client.post(
        "/api/jobs",
        data={
            "title": "E2E fanout write",
            "description": "Two steps, two targets, artifact-first writes",
            "write_execution_mode": "platform",
            "output_artifact_format": "jsonl",
            "output_contract": json.dumps(contract),
        },
        headers=_auth_headers(business_user),
    )
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    # Create a second active agent to form a true multi-step flow.
    dev = developer_user["user"]
    second_agent = Agent(
        developer_id=dev.id,
        name="E2E Second Agent",
        description="Secondary integration agent",
        status=AgentStatus.ACTIVE,
        price_per_task=10.0,
        price_per_communication=1.0,
        api_endpoint="https://example.com/v1/chat/completions",
        api_key="sk-test-2",
        llm_model="gpt-4o-mini",
        a2a_enabled=False,
    )
    integration_db_session.add(second_agent)
    integration_db_session.commit()
    integration_db_session.refresh(second_agent)

    flow = integration_client.post(
        f"/api/jobs/{job_id}/workflow/manual",
        json=[
            {"agent_id": sample_agent.id, "step_order": 1},
            {"agent_id": second_agent.id, "step_order": 2},
        ],
        headers=_auth_headers(business_user),
    )
    assert flow.status_code == 200, flow.text
    approve = integration_client.post(f"/api/jobs/{job_id}/approve", headers=_auth_headers(business_user))
    assert approve.status_code == 200, approve.text

    async def _step_output(*args, **kwargs):
        return {"records": [{"customer_id": "C-100", "decision": "review", "confidence": 0.81}]}

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_step_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock) as mock_call_tool:
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None
        mock_call_tool.return_value = {"content": [{"type": "text", "text": "write-ok"}], "isError": False}

        execute = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert execute.status_code == 200, execute.text
        asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))

    status = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth_headers(business_user))
    assert status.status_code == 200, status.text
    steps = status.json().get("workflow_steps") or []
    assert len(steps) == 2

    for step in steps:
        out = json.loads(step["output_data"])
        assert (out.get("artifact_ref") or {}).get("format") == "jsonl"
        assert out.get("write_execution_mode") == "platform"
        assert len(out.get("write_results") or []) == 2

    # 2 workflow steps x 2 write targets = 4 write-tool calls.
    assert mock_call_tool.await_count == 4


def test_e2e_platform_mode_write_target_failure_is_captured_in_step_output(
    integration_client: TestClient, integration_db_session, business_user, sample_agent
):
    contract = {
        "version": "1.0",
        "write_targets": [
            {
                "tool_name": "platform_1_snowflake_kyc_results",
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": ["customer_id"],
                "target": {"database": "BANK", "schema": "RISK", "table": "KYC_AML_DECISIONS"},
            },
            {
                "tool_name": "platform_1_s3_audit_archive",
                "operation_type": "append",
                "write_mode": "append",
                "merge_keys": [],
                "target": {"bucket": "audit", "prefix": "jobs/kyc"},
            },
        ],
    }
    create = integration_client.post(
        "/api/jobs",
        data={
            "title": "E2E write failure capture",
            "description": "Platform mode should capture partial write failures",
            "write_execution_mode": "platform",
            "output_artifact_format": "jsonl",
            "output_contract": json.dumps(contract),
        },
        headers=_auth_headers(business_user),
    )
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    flow = integration_client.post(
        f"/api/jobs/{job_id}/workflow/manual",
        json=[{"agent_id": sample_agent.id, "step_order": 1}],
        headers=_auth_headers(business_user),
    )
    assert flow.status_code == 200, flow.text
    approve = integration_client.post(f"/api/jobs/{job_id}/approve", headers=_auth_headers(business_user))
    assert approve.status_code == 200, approve.text

    async def _agent_output(*args, **kwargs):
        return {"records": [{"customer_id": "C-777", "decision": "escalate", "confidence": 0.95}]}

    async def _write_side_effect(*args, **kwargs):
        tool_name = kwargs.get("tool_name")
        if tool_name == "platform_1_s3_audit_archive":
            raise RuntimeError("simulated write failure")
        return {"content": [{"type": "text", "text": "write-ok"}], "isError": False}

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_agent_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock, side_effect=_write_side_effect) as mock_call_tool:
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None
        execute = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert execute.status_code == 200, execute.text
        with pytest.raises(Exception, match="Workflow step 1 failed: simulated write failure"):
            asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))
        assert mock_call_tool.await_count == 2

    status = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth_headers(business_user))
    assert status.status_code == 200, status.text
    assert status.json().get("status") == "failed"
    step = (status.json().get("workflow_steps") or [])[0]
    out = json.loads(step["output_data"])
    assert (out.get("artifact_ref") or {}).get("format") == "jsonl"
    results = out.get("write_results") or []
    assert len(results) == 2
    assert len([r for r in results if r.get("status") == "success"]) == 1
    assert len([r for r in results if r.get("status") == "failed"]) == 1
    assert "simulated write failure" in (out.get("error") or "")


def test_e2e_platform_mode_continue_policy_allows_partial_write_success(
    integration_client: TestClient, integration_db_session, business_user, sample_agent
):
    contract = {
        "version": "1.0",
        "write_policy": {
            "on_write_error": "continue",
            "min_successful_targets": 1,
        },
        "write_targets": [
            {
                "tool_name": "platform_1_snowflake_kyc_results",
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": ["customer_id"],
                "target": {"database": "BANK", "schema": "RISK", "table": "KYC_AML_DECISIONS"},
            },
            {
                "tool_name": "platform_1_s3_audit_archive",
                "operation_type": "append",
                "write_mode": "append",
                "merge_keys": [],
                "target": {"bucket": "audit", "prefix": "jobs/kyc"},
            },
        ],
    }
    create = integration_client.post(
        "/api/jobs",
        data={
            "title": "E2E write continue policy",
            "description": "Continue-on-error with minimum successful writes",
            "write_execution_mode": "platform",
            "output_artifact_format": "jsonl",
            "output_contract": json.dumps(contract),
        },
        headers=_auth_headers(business_user),
    )
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    flow = integration_client.post(
        f"/api/jobs/{job_id}/workflow/manual",
        json=[{"agent_id": sample_agent.id, "step_order": 1}],
        headers=_auth_headers(business_user),
    )
    assert flow.status_code == 200, flow.text
    approve = integration_client.post(f"/api/jobs/{job_id}/approve", headers=_auth_headers(business_user))
    assert approve.status_code == 200, approve.text

    async def _agent_output(*args, **kwargs):
        return {"records": [{"customer_id": "C-901", "decision": "nfa", "confidence": 0.72}]}

    async def _write_side_effect(*args, **kwargs):
        tool_name = kwargs.get("tool_name")
        if tool_name == "platform_1_s3_audit_archive":
            raise RuntimeError("simulated write failure")
        return {"content": [{"type": "text", "text": "write-ok"}], "isError": False}

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_agent_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock, side_effect=_write_side_effect):
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None
        execute = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert execute.status_code == 200, execute.text
        asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))

    status = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth_headers(business_user))
    assert status.status_code == 200, status.text
    assert status.json().get("status") == "completed"
    step = (status.json().get("workflow_steps") or [])[0]
    out = json.loads(step["output_data"])
    assert (out.get("artifact_ref") or {}).get("format") == "jsonl"
    assert out.get("write_policy", {}).get("on_write_error") == "continue"
    assert out.get("write_policy", {}).get("min_successful_targets") == 1
    results = out.get("write_results") or []
    assert len(results) == 2
    assert len([r for r in results if r.get("status") == "success"]) == 1
    assert len([r for r in results if r.get("status") == "failed"]) == 1


@pytest.mark.parametrize(
    "tool_name,target,merge_keys",
    [
        (
            "platform_1_sqlserver_job_metrics",
            {"database": "SANDHI_AI", "schema": "dbo", "table": "job_metrics"},
            ["job_id"],
        ),
        (
            "platform_1_mysql_job_metrics",
            {"database": "sandhi_app", "table": "job_metrics"},
            ["job_id"],
        ),
        (
            "platform_1_databricks_job_metrics",
            {"catalog": "samples", "schema": "bakehouse", "table": "job_metrics"},
            ["job_id"],
        ),
        (
            "platform_1_snowflake_job_metrics",
            {"database": "SANDHI_AI", "schema": "PUBLIC", "table": "JOB_METRICS"},
            ["JOB_ID"],
        ),
        (
            "platform_1_bigquery_job_metrics",
            {"project": "sandhi-dev", "dataset": "analytics", "table": "job_metrics"},
            ["job_id"],
        ),
    ],
)
def test_e2e_platform_mode_write_contract_targets_all_sql_warehouses(
    integration_client: TestClient,
    integration_db_session,
    business_user,
    sample_agent,
    tool_name,
    target,
    merge_keys,
):
    target_with_bootstrap = {
        **target,
        "bootstrap_sql": "CREATE TABLE IF NOT EXISTS job_metrics (job_id BIGINT, total_jobs BIGINT)",
    }
    contract = {
        "version": "1.0",
        "write_targets": [
            {
                "tool_name": tool_name,
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": merge_keys,
                "target": target_with_bootstrap,
            }
        ],
    }
    job_id = _create_job_with_custom_output_contract(
        integration_client, business_user, write_execution_mode="platform", contract=contract
    )
    _prepare_single_step_workflow(integration_client, business_user, job_id, sample_agent.id)

    async def _agent_output(*args, **kwargs):
        return {"records": [{"job_id": 24, "total_jobs": 21}]}

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_agent_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock) as mock_call_tool:
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None
        mock_call_tool.return_value = {"content": [{"type": "text", "text": "write-ok"}], "isError": False}

        execute = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert execute.status_code == 200, execute.text
        asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))

        assert mock_call_tool.await_count == 1
        call = mock_call_tool.await_args
        kwargs = call.kwargs
        assert kwargs["tool_name"] == tool_name
        assert kwargs["arguments"]["operation_type"] == "upsert"
        assert kwargs["arguments"]["write_mode"] == "upsert"
        assert kwargs["arguments"]["merge_keys"] == merge_keys

        # bootstrap_sql is removed from runtime target and passed only via trusted_bootstrap signature envelope.
        assert "bootstrap_sql" not in kwargs["arguments"]["target"]
        assert kwargs["arguments"]["target"] == target
        secret_configured = bool((getattr(settings, "MCP_INTERNAL_SECRET", "") or "").strip())
        if secret_configured:
            assert kwargs["arguments"]["trusted_bootstrap"]["bootstrap_sql"].startswith("CREATE TABLE IF NOT EXISTS")
            assert kwargs["arguments"]["trusted_bootstrap"]["sig"]
        else:
            assert "trusted_bootstrap" not in kwargs["arguments"]


@pytest.mark.parametrize(
    "tool_name,target,merge_keys,error_slug",
    [
        (
            "platform_1_sqlserver_job_metrics",
            {"database": "SANDHI_AI", "schema": "dbo", "table": "job_metrics"},
            ["job_id"],
            "sqlserver",
        ),
        (
            "platform_1_mysql_job_metrics",
            {"database": "sandhi_app", "table": "job_metrics"},
            ["job_id"],
            "mysql",
        ),
        (
            "platform_1_databricks_job_metrics",
            {"catalog": "samples", "schema": "bakehouse", "table": "job_metrics"},
            ["job_id"],
            "databricks",
        ),
        (
            "platform_1_snowflake_job_metrics",
            {"database": "SANDHI_AI", "schema": "PUBLIC", "table": "JOB_METRICS"},
            ["JOB_ID"],
            "snowflake",
        ),
        (
            "platform_1_bigquery_job_metrics",
            {"project": "sandhi-dev", "dataset": "analytics", "table": "job_metrics"},
            ["job_id"],
            "bigquery",
        ),
    ],
)
def test_e2e_platform_mode_write_failure_captured_per_database_target(
    integration_client: TestClient,
    integration_db_session,
    business_user,
    sample_agent,
    tool_name,
    target,
    merge_keys,
    error_slug,
):
    """Each DB-shaped write target records MCP failure in write_results (default fail_job policy)."""
    contract = {
        "version": "1.0",
        "write_targets": [
            {
                "tool_name": tool_name,
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": merge_keys,
                "target": target,
            }
        ],
    }
    job_id = _create_job_with_custom_output_contract(
        integration_client, business_user, write_execution_mode="platform", contract=contract
    )
    _prepare_single_step_workflow(integration_client, business_user, job_id, sample_agent.id)

    async def _agent_output(*args, **kwargs):
        return {"records": [{"job_id": 1, "total_jobs": 1}]}

    err_msg = f"simulated_{error_slug}_artifact_write_failure"

    async def _write_raises(*args, **kwargs):
        if kwargs.get("tool_name") == tool_name:
            raise RuntimeError(err_msg)
        raise AssertionError(f"unexpected tool_name={kwargs.get('tool_name')!r}")

    with patch("api.routes.jobs.queue_job_execution") as mock_queue, patch(
        "services.agent_executor.AgentExecutor._execute_agent", new_callable=AsyncMock, side_effect=_agent_output
    ), patch("services.agent_executor.mcp_call_tool", new_callable=AsyncMock, side_effect=_write_raises) as mock_call_tool:
        from services.agent_executor import AgentExecutor

        mock_queue.return_value = None
        execute = integration_client.post(f"/api/jobs/{job_id}/execute", headers=_auth_headers(business_user))
        assert execute.status_code == 200, execute.text
        with pytest.raises(Exception, match=f"Workflow step 1 failed:.*{err_msg}"):
            asyncio.run(AgentExecutor(integration_db_session).execute_job(job_id))
        assert mock_call_tool.await_count == 1

    status = integration_client.get(f"/api/jobs/{job_id}/status", headers=_auth_headers(business_user))
    assert status.status_code == 200, status.text
    assert status.json().get("status") == "failed"
    step = (status.json().get("workflow_steps") or [])[0]
    out = json.loads(step["output_data"])
    assert (out.get("artifact_ref") or {}).get("format") == "jsonl"
    results = out.get("write_results") or []
    assert len(results) == 1
    assert results[0].get("tool_name") == tool_name
    assert results[0].get("status") == "failed"
    assert err_msg in (results[0].get("error") or "")
    assert err_msg in (out.get("error") or "")
