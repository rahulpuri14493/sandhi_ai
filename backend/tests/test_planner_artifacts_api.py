"""HTTP tests for GET /api/jobs/{job_id}/planner-artifacts and .../raw."""

import json
import uuid
from typing import Optional

from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from models.agent import Agent, AgentStatus
from models.job import Job, JobPlannerArtifact, JobStatus, WorkflowStep
from models.mcp_server import MCPToolConfig, MCPToolType
from models.user import User, UserRole


def _biz_headers(db_session, suffix: Optional[str] = None):
    s = suffix or uuid.uuid4().hex[:8]
    user = User(
        email=f"biz_pa_{s}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.id})
    return user, {"Authorization": f"Bearer {token}"}


def test_list_planner_artifacts_requires_auth(client: TestClient):
    r = client.get("/api/jobs/1/planner-artifacts")
    assert r.status_code in (401, 403)


def test_list_planner_artifacts_job_not_found(client: TestClient, db_session):
    _, headers = _biz_headers(db_session)
    r = client.get("/api/jobs/999999/planner-artifacts", headers=headers)
    assert r.status_code == 404


def test_list_planner_artifacts_forbidden_other_business(client: TestClient, db_session):
    owner, _ = _biz_headers(db_session, "owner")
    other, other_headers = _biz_headers(db_session, "other")
    job = Job(business_id=owner.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=other_headers)
    assert r.status_code == 403


def test_list_planner_artifacts_happy_path(client: TestClient, db_session, tmp_path):
    biz, headers = _biz_headers(db_session, "happy")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f = tmp_path / "audit.json"
    f.write_text('{"x": 1}', encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="task_split",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=9,
    )
    db_session.add(art)
    db_session.commit()

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["artifact_type"] == "task_split"
    assert data["items"][0]["job_id"] == job.id
    assert data["items"][0]["id"] == art.id


def test_planner_pipeline_composes_latest_per_type(client: TestClient, db_session, tmp_path):
    """GET planner-pipeline returns latest row per artifact type (id tie-break when created_at ties)."""
    biz, headers = _biz_headers(db_session, "pipe")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f_old = tmp_path / "ts_old.json"
    f_old.write_text('{"task_split": "old"}', encoding="utf-8")
    f_new = tmp_path / "ts_new.json"
    f_new.write_text('{"task_split": "new"}', encoding="utf-8")
    f_brd = tmp_path / "brd.json"
    f_brd.write_text('{"analysis": "x"}', encoding="utf-8")

    db_session.add_all(
        [
            JobPlannerArtifact(
                job_id=job.id,
                artifact_type="task_split",
                storage="local",
                bucket=None,
                object_key=str(f_old),
                byte_size=1,
            ),
            JobPlannerArtifact(
                job_id=job.id,
                artifact_type="task_split",
                storage="local",
                bucket=None,
                object_key=str(f_new),
                byte_size=1,
            ),
            JobPlannerArtifact(
                job_id=job.id,
                artifact_type="brd_analysis",
                storage="local",
                bucket=None,
                object_key=str(f_brd),
                byte_size=1,
            ),
        ]
    )
    db_session.commit()

    r = client.get(f"/api/jobs/{job.id}/planner-pipeline", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["schema_version"] == "planner_pipeline.v1"
    assert data["job_id"] == job.id
    assert data["task_split"] == {"task_split": "new"}
    assert data["brd_analysis"] == {"analysis": "x"}
    assert data["tool_suggestion"] is None
    assert data["artifact_ids"]["brd_analysis"] is not None
    assert data["artifact_ids"]["task_split"] is not None
    assert data["artifact_ids"]["tool_suggestion"] is None


def test_planner_pipeline_requires_auth(client: TestClient):
    r = client.get("/api/jobs/1/planner-pipeline")
    assert r.status_code in (401, 403)


def test_planner_pipeline_job_not_found(client: TestClient, db_session):
    _, headers = _biz_headers(db_session)
    r = client.get("/api/jobs/999999/planner-pipeline", headers=headers)
    assert r.status_code == 404


def test_planner_pipeline_forbidden_other_business(client: TestClient, db_session):
    owner, _ = _biz_headers(db_session, "pipe_owner")
    other, other_headers = _biz_headers(db_session, "pipe_other")
    job = Job(business_id=owner.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/planner-pipeline", headers=other_headers)
    assert r.status_code == 403


def test_planner_pipeline_empty_job_returns_null_payloads(client: TestClient, db_session):
    biz, headers = _biz_headers(db_session, "pipe_empty")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/planner-pipeline", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["schema_version"] == "planner_pipeline.v1"
    assert data["job_id"] == job.id
    assert data["brd_analysis"] is None
    assert data["task_split"] is None
    assert data["tool_suggestion"] is None
    assert data["artifact_ids"]["brd_analysis"] is None
    assert data["artifact_ids"]["task_split"] is None
    assert data["artifact_ids"]["tool_suggestion"] is None


def test_planner_pipeline_invalid_json_returns_null_payload_with_artifact_id(
    client: TestClient, db_session, tmp_path
):
    biz, headers = _biz_headers(db_session, "pipe_badjson")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f = tmp_path / "corrupt.json"
    f.write_text("{broken", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="brd_analysis",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=8,
    )
    db_session.add(art)
    db_session.commit()

    r = client.get(f"/api/jobs/{job.id}/planner-pipeline", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["brd_analysis"] is None
    assert data["artifact_ids"]["brd_analysis"] == art.id


def test_planner_pipeline_developer_on_workflow_can_read(client: TestClient, db_session, tmp_path):
    biz, _ = _biz_headers(db_session, "pipe_devbiz")
    dev = User(
        email=f"dev_pipe_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    dev_headers = {"Authorization": f"Bearer {create_access_token({'sub': dev.id})}"}

    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    agent = Agent(
        developer_id=dev.id,
        name="Agent",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1/chat",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        input_data="{}",
        status="pending",
    )
    db_session.add(step)
    db_session.commit()

    f = tmp_path / "pipe_dev.json"
    f.write_text('{"tasks": [1]}', encoding="utf-8")
    db_session.add(
        JobPlannerArtifact(
            job_id=job.id,
            artifact_type="task_split",
            storage="local",
            bucket=None,
            object_key=str(f),
            byte_size=1,
        )
    )
    db_session.commit()

    r = client.get(f"/api/jobs/{job.id}/planner-pipeline", headers=dev_headers)
    assert r.status_code == 200, r.text
    assert r.json()["task_split"] == {"tasks": [1]}


def test_download_planner_artifact_raw_requires_auth(client: TestClient):
    r = client.get("/api/jobs/1/planner-artifacts/1/raw")
    assert r.status_code in (401, 403)


def test_download_planner_artifact_raw_job_not_found(client: TestClient, db_session):
    _, headers = _biz_headers(db_session)
    r = client.get("/api/jobs/999999/planner-artifacts/1/raw", headers=headers)
    assert r.status_code == 404


def test_download_planner_artifact_raw_wrong_job_id_returns_404(client: TestClient, db_session, tmp_path):
    """Artifact exists for job A; requesting with job B id must not leak the row."""
    biz, headers = _biz_headers(db_session, "wrongjob")
    job_a = Job(business_id=biz.id, title="A", status=JobStatus.DRAFT, conversation=json.dumps([]))
    job_b = Job(business_id=biz.id, title="B", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add_all([job_a, job_b])
    db_session.commit()
    db_session.refresh(job_a)
    db_session.refresh(job_b)

    f = tmp_path / "only_a.json"
    f.write_text("{}", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job_a.id,
        artifact_type="task_split",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=2,
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    r = client.get(f"/api/jobs/{job_b.id}/planner-artifacts/{art.id}/raw", headers=headers)
    assert r.status_code == 404


def test_download_planner_artifact_raw_forbidden_other_business(client: TestClient, db_session, tmp_path):
    owner, _ = _biz_headers(db_session, "rawown")
    other, other_headers = _biz_headers(db_session, "rawoth")
    job = Job(business_id=owner.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f = tmp_path / "p.json"
    f.write_text("{}", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="brd_analysis",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=2,
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts/{art.id}/raw", headers=other_headers)
    assert r.status_code == 403


def test_download_planner_artifact_raw_cache_hit_skips_storage_read(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _biz_headers(db_session, "cachehit")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f = tmp_path / "disk.json"
    f.write_text("{}", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="tool_suggestion",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=2,
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    import api.routes.jobs as jobs_mod

    cached = b'{"from":"redis"}'

    def no_storage_read(_row):
        raise AssertionError("read_planner_artifact_bytes should not run on cache hit")

    monkeypatch.setattr(jobs_mod, "get_cached_planner_raw", lambda jid, aid: cached if jid == job.id and aid == art.id else None)
    monkeypatch.setattr(jobs_mod, "read_planner_artifact_bytes", no_storage_read)
    monkeypatch.setattr(jobs_mod, "set_cached_planner_raw", lambda *a, **k: None)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts/{art.id}/raw", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"from": "redis"}


def test_get_planner_status_requires_auth(client: TestClient):
    r = client.get("/api/jobs/planner/status")
    assert r.status_code in (401, 403)


def test_get_planner_status_returns_shape(monkeypatch, client: TestClient, db_session):
    _, headers = _biz_headers(db_session, "plstat")
    import services.planner_llm as plm

    monkeypatch.setattr(plm, "is_agent_planner_configured", lambda: False)
    monkeypatch.setattr(
        plm,
        "get_planner_public_meta",
        lambda: {"provider": "openai_compatible", "model": "gpt-4o-mini", "base_url_configured": False},
    )

    r = client.get("/api/jobs/planner/status", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data.get("configured") is False
    assert "provider" in data


def test_list_planner_artifacts_empty_items(client: TestClient, db_session):
    biz, headers = _biz_headers(db_session, "emptyart")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_download_planner_artifact_raw_artifact_not_found(client: TestClient, db_session):
    biz, headers = _biz_headers(db_session, "rawnf")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts/999999/raw", headers=headers)
    assert r.status_code == 404


def test_suggest_workflow_tools_persists_artifact_with_mocked_persist_file(monkeypatch, client: TestClient, db_session, tmp_path):
    """POST suggest-workflow-tools writes planner JSON via persist_file (mocked)."""
    biz, headers = _biz_headers(db_session, "suggestpersist")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    dev = User(
        email=f"dev_sp_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    agent = Agent(
        developer_id=dev.id,
        name="Split",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    tool = MCPToolConfig(
        user_id=biz.id,
        tool_type=MCPToolType.POSTGRES,
        name="PG",
        encrypted_config="{}",
        is_active=True,
    )
    db_session.add(tool)
    db_session.commit()

    import api.routes.jobs as jobs_mod
    import services.planner_artifact_storage as pas_mod

    async def fake_persist(name, data, content_type, job_id=None):
        dest = tmp_path / name
        dest.write_bytes(data)
        return {"storage": "local", "path": str(dest)}

    async def fake_suggest(**kwargs):
        audit = kwargs.get("llm_audit")
        if audit is not None:
            audit["raw_llm_response"] = '[{"agent_index": 0, "platform_tool_ids": [], "rationale": "t"}]'
            audit["source"] = "agent_endpoint"
        return {
            "step_suggestions": [{"agent_index": 0, "platform_tool_ids": [], "rationale": "t"}],
            "output_contract_stub": {"version": "1.0", "write_targets": []},
            "fallback_used": False,
        }

    monkeypatch.setattr(pas_mod, "persist_file", fake_persist)
    monkeypatch.setattr(jobs_mod, "suggest_tool_assignments_for_agents", fake_suggest)

    r = client.post(
        f"/api/jobs/{job.id}/suggest-workflow-tools",
        headers=headers,
        json={"agent_ids": [agent.id]},
    )
    assert r.status_code == 200, r.text

    r2 = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=headers)
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert len(items) == 1
    assert items[0]["artifact_type"] == "tool_suggestion"


def test_suggest_workflow_tools_no_persist_when_no_llm_audit_raw(monkeypatch, client: TestClient, db_session):
    """Fallback path without LLM text must not insert planner artifact rows."""
    biz, headers = _biz_headers(db_session, "nosuggestpersist")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    dev = User(
        email=f"dev_ns_{uuid.uuid4().hex[:8]}@example.com",
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
        api_endpoint="https://example.com/v1",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    tool = MCPToolConfig(
        user_id=biz.id,
        tool_type=MCPToolType.POSTGRES,
        name="PG",
        encrypted_config="{}",
        is_active=True,
    )
    db_session.add(tool)
    db_session.commit()

    import api.routes.jobs as jobs_mod

    async def fake_suggest(**kwargs):
        return {
            "step_suggestions": [],
            "output_contract_stub": None,
            "fallback_used": True,
        }

    monkeypatch.setattr(jobs_mod, "suggest_tool_assignments_for_agents", fake_suggest)

    r = client.post(
        f"/api/jobs/{job.id}/suggest-workflow-tools",
        headers=headers,
        json={"agent_ids": [agent.id]},
    )
    assert r.status_code == 200, r.text
    r2 = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=headers)
    assert r2.json()["items"] == []


def test_download_planner_artifact_raw_happy_path(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _biz_headers(db_session, "rawok")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f = tmp_path / "blob.json"
    f.write_text("unused", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="brd_analysis",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=6,
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "get_cached_planner_raw", lambda jid, aid: None)
    monkeypatch.setattr(jobs_mod, "set_cached_planner_raw", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod, "read_planner_artifact_bytes", lambda row: b'{"patched": true}')

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts/{art.id}/raw", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("application/json")
    assert r.json() == {"patched": True}


def test_download_planner_artifact_raw_storage_error_503(monkeypatch, client: TestClient, db_session, tmp_path):
    biz, headers = _biz_headers(db_session, "raw503")
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="tool_suggestion",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=2,
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    import api.routes.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "get_cached_planner_raw", lambda jid, aid: None)

    def boom(_row):
        raise OSError("storage down")

    monkeypatch.setattr(jobs_mod, "read_planner_artifact_bytes", boom)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts/{art.id}/raw", headers=headers)
    assert r.status_code == 503


def test_list_planner_artifacts_developer_not_on_workflow_forbidden(client: TestClient, db_session, tmp_path):
    biz, biz_headers = _biz_headers(db_session, "dforb")
    dev = User(
        email=f"dev_nostep_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    dev_headers = {"Authorization": f"Bearer {create_access_token({'sub': dev.id})}"}

    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=dev_headers)
    assert r.status_code == 403


def test_list_planner_artifacts_developer_on_workflow_can_read(client: TestClient, db_session, tmp_path):
    biz, _ = _biz_headers(db_session, "dbiz")
    dev = User(
        email=f"dev_pa_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    dev_headers = {"Authorization": f"Bearer {create_access_token({'sub': dev.id})}"}

    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    agent = Agent(
        developer_id=dev.id,
        name="Agent",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://example.com/v1/chat",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        input_data="{}",
        status="pending",
    )
    db_session.add(step)
    db_session.commit()

    f = tmp_path / "dev.json"
    f.write_text("{}", encoding="utf-8")
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="brd_analysis",
        storage="local",
        bucket=None,
        object_key=str(f),
        byte_size=2,
    )
    db_session.add(art)
    db_session.commit()

    r = client.get(f"/api/jobs/{job.id}/planner-artifacts", headers=dev_headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["id"] == art.id
