"""Direct unit tests for api.routes.jobs helpers (max statement coverage, no full app flows)."""

import io
import json
import uuid
import zipfile
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import api.routes.jobs as jm
from models.agent import Agent, AgentStatus
from models.job import Job, JobStatus, WorkflowStep
from models.mcp_server import MCPServerConnection, MCPToolConfig, MCPToolType
from models.user import User, UserRole


# --- Form / contract helpers ---


def test_parse_int_list_form():
    assert jm._parse_int_list_form(None) is None
    assert jm._parse_int_list_form("") is None
    assert jm._parse_int_list_form("   ") is None
    assert jm._parse_int_list_form("[1, 2]") == [1, 2]
    assert jm._parse_int_list_form("not json") is None
    assert jm._parse_int_list_form("{}") is None


def test_validate_tool_visibility():
    assert jm._validate_tool_visibility(None) is None
    assert jm._validate_tool_visibility("") is None
    assert jm._validate_tool_visibility("  FULL  ") == "full"
    assert jm._validate_tool_visibility("names_only") == "names_only"
    assert jm._validate_tool_visibility("NONE") == "none"
    with pytest.raises(HTTPException) as e:
        jm._validate_tool_visibility("bad")
    assert e.value.status_code == 400


def test_validate_write_execution_mode():
    assert jm._validate_write_execution_mode(None) == "platform"
    assert jm._validate_write_execution_mode("  AGENT ") == "agent"
    assert jm._validate_write_execution_mode("ui_only") == "ui_only"
    with pytest.raises(HTTPException):
        jm._validate_write_execution_mode("nope")


def test_validate_output_artifact_format():
    assert jm._validate_output_artifact_format(None) == "jsonl"
    assert jm._validate_output_artifact_format("JSON") == "json"
    with pytest.raises(HTTPException):
        jm._validate_output_artifact_format("xml")


def test_parse_json_form():
    assert jm._parse_json_form(None) is None
    assert jm._parse_json_form("") is None
    assert jm._parse_json_form("  ") is None
    assert jm._parse_json_form('{"a":1}') == {"a": 1}
    with pytest.raises(HTTPException) as exc:
        jm._parse_json_form("{")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        jm._parse_json_form("[1,2]")
    assert "object" in (exc.value.detail or "").lower()


def test_validate_output_contract_policy():
    assert jm._validate_output_contract_policy(None) is None
    with pytest.raises(HTTPException):
        jm._validate_output_contract_policy("x")  # type: ignore[arg-type]
    base = {"write_targets": []}
    assert jm._validate_output_contract_policy(base) == base
    with pytest.raises(HTTPException):
        jm._validate_output_contract_policy({"write_policy": "bad"})
    with pytest.raises(HTTPException):
        jm._validate_output_contract_policy(
            {"write_policy": {"on_write_error": "x"}}
        )
    ok = jm._validate_output_contract_policy(
        {"write_policy": {"on_write_error": "continue", "min_successful_targets": 0}}
    )
    assert ok["write_policy"]["on_write_error"] == "continue"
    with pytest.raises(HTTPException):
        jm._validate_output_contract_policy(
            {"write_policy": {"min_successful_targets": "x"}}
        )
    with pytest.raises(HTTPException):
        jm._validate_output_contract_policy(
            {"write_policy": {"min_successful_targets": -1}}
        )


def test_parse_contract_json():
    assert jm._parse_contract_json(None) is None
    assert jm._parse_contract_json("") is None
    assert jm._parse_contract_json("not json") is None
    assert jm._parse_contract_json("[]") is None
    assert jm._parse_contract_json('{"z":1}') == {"z": 1}


# --- DB-backed helpers ---


def test_validate_allowed_tools_empty_explicit_lists(db_session):
    from core.security import get_password_hash

    biz = User(
        email=f"v_{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    db_session.add(biz)
    db_session.commit()
    db_session.refresh(biz)
    op, oc = jm._validate_allowed_tools(db_session, biz.id, [], [])
    assert op == [] and oc == []


def test_validate_allowed_tools_valid_and_invalid_platform(db_session):
    biz = User(
        email=f"v2_{uuid.uuid4().hex[:8]}@t.com",
        password_hash="x",
        role=UserRole.BUSINESS,
    )
    db_session.add(biz)
    db_session.commit()
    db_session.refresh(biz)
    cfg = MCPToolConfig(
        user_id=biz.id,
        tool_type=MCPToolType.POSTGRES,
        name="p",
        encrypted_config="{}",
        is_active=True,
    )
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    good, _ = jm._validate_allowed_tools(db_session, biz.id, [cfg.id], None)
    assert set(good) == {cfg.id}
    with pytest.raises(HTTPException) as e:
        jm._validate_allowed_tools(db_session, biz.id, [cfg.id, 999999], None)
    assert e.value.status_code == 400
    assert "platform" in (e.value.detail or "").lower()


def test_validate_allowed_tools_invalid_connection(db_session):
    biz = User(
        email=f"v3_{uuid.uuid4().hex[:8]}@t.com",
        password_hash="x",
        role=UserRole.BUSINESS,
    )
    db_session.add(biz)
    db_session.commit()
    db_session.refresh(biz)
    conn = MCPServerConnection(
        user_id=biz.id,
        name="c",
        base_url="https://mcp.example/mcp",
        endpoint_path="/mcp",
        is_active=True,
    )
    db_session.add(conn)
    db_session.commit()
    db_session.refresh(conn)
    _, oc = jm._validate_allowed_tools(db_session, biz.id, None, [conn.id])
    assert oc == [conn.id]
    with pytest.raises(HTTPException) as e:
        jm._validate_allowed_tools(db_session, biz.id, None, [conn.id, 888888])
    assert "connection" in (e.value.detail or "").lower()


def test_user_can_access_job_business_and_developer(db_session):
    from core.security import get_password_hash

    biz = User(
        email=f"ub_{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    dev = User(
        email=f"ud_{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.DEVELOPER,
    )
    db_session.add_all([biz, dev])
    db_session.commit()
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert jm._user_can_access_job(job, biz, db_session) is True
    assert jm._user_can_access_job(job, dev, db_session) is False

    ag = Agent(
        developer_id=dev.id,
        name="ag",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.0,
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    db_session.add(
        WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}")
    )
    db_session.commit()
    assert jm._user_can_access_job(job, dev, db_session) is True


def test_get_first_hired_agent_for_job_variants(db_session):
    from core.security import get_password_hash

    biz = User(
        email=f"gh_{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    dev = User(
        email=f"gd_{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.DEVELOPER,
    )
    db_session.add_all([biz, dev])
    db_session.commit()
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert jm._get_first_hired_agent_for_job(db_session, job.id) is None

    ag = Agent(
        developer_id=dev.id,
        name="ag",
        description="d",
        status=AgentStatus.ACTIVE,
        price_per_task=1.0,
        price_per_communication=0.0,
        api_endpoint="  https://api.example/v1  ",
        api_key=" k ",
        llm_model="gpt-4o-mini",
        temperature=0.5,
        a2a_enabled=True,
    )
    db_session.add(ag)
    db_session.commit()
    db_session.refresh(ag)
    db_session.add(
        WorkflowStep(job_id=job.id, agent_id=ag.id, step_order=1, input_data="{}")
    )
    db_session.commit()
    tup = jm._get_first_hired_agent_for_job(db_session, job.id)
    assert tup is not None
    assert tup[0] == "https://api.example/v1"
    assert tup[1] == "k"
    assert tup[2] == "gpt-4o-mini"
    assert tup[3] == 0.5
    assert tup[4] is True

    ag.api_endpoint = "   "
    db_session.commit()
    assert jm._get_first_hired_agent_for_job(db_session, job.id) is None


def test_transition_job_status_if_current(db_session):
    from core.security import get_password_hash

    biz = User(
        email=f"tr_{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    db_session.add(biz)
    db_session.commit()
    job = Job(business_id=biz.id, title="J", status=JobStatus.DRAFT)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    ok = jm._transition_job_status_if_current(
        db_session,
        job_id=job.id,
        business_id=biz.id,
        from_statuses=[JobStatus.DRAFT],
        to_status=JobStatus.PENDING_APPROVAL,
        extra_updates={"failure_reason": None},
    )
    assert ok is True
    db_session.refresh(job)
    assert job.status == JobStatus.PENDING_APPROVAL
    ok2 = jm._transition_job_status_if_current(
        db_session,
        job_id=job.id,
        business_id=biz.id,
        from_statuses=[JobStatus.DRAFT],
        to_status=JobStatus.APPROVED,
    )
    assert ok2 is False


# --- Upload processing ---


@pytest.mark.asyncio
async def test_process_one_upload_single_file_ok(monkeypatch):
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 10000)
    monkeypatch.setattr(jm, "persist_file", AsyncMock(return_value={"name": "a.txt", "path": "/x"}))

    class F:
        filename = "a.txt"
        content_type = "text/plain"

        async def read(self):
            return b"hello"

    out = await jm._process_one_upload(F(), job_id=1)
    assert out == [{"name": "a.txt", "path": "/x"}]


@pytest.mark.asyncio
async def test_process_one_upload_too_large(monkeypatch):
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 2)

    class F:
        filename = "big.txt"
        content_type = "text/plain"

        async def read(self):
            return b"abc"

    with pytest.raises(HTTPException) as e:
        await jm._process_one_upload(F())
    assert e.value.status_code == 413


@pytest.mark.asyncio
async def test_process_one_upload_persist_fails(monkeypatch):
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)

    async def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(jm, "persist_file", boom)

    class F:
        filename = "a.txt"
        content_type = "text/plain"

        async def read(self):
            return b"x"

    with pytest.raises(HTTPException) as e:
        await jm._process_one_upload(F())
    assert e.value.status_code == 500


@pytest.mark.asyncio
async def test_process_one_upload_zip_extracts_txt(monkeypatch):
    monkeypatch.setattr(jm.settings, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)
    monkeypatch.setattr(jm.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 1)
    calls = []

    async def persist(name, data, ctype, job_id=None):
        calls.append(name)
        return {"name": name, "path": f"/{name}"}

    monkeypatch.setattr(jm, "persist_file", persist)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inner/readme.txt", b"hello zip")
    raw = buf.getvalue()

    class F:
        filename = "bundle.zip"
        content_type = "application/zip"

        async def read(self):
            return raw

    out = await jm._process_one_upload(F(), job_id=5)
    assert any(o["name"] == "readme.txt" for o in out)


@pytest.mark.asyncio
async def test_process_one_upload_zip_bad_file(monkeypatch):
    monkeypatch.setattr(jm.settings, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)
    monkeypatch.setattr(jm.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 1)

    class F:
        filename = "bad.zip"
        content_type = "application/zip"

        async def read(self):
            return b"not a zip"

    with pytest.raises(HTTPException) as e:
        await jm._process_one_upload(F())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_process_one_upload_zip_retry_then_ok(monkeypatch):
    monkeypatch.setattr(jm.settings, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)
    monkeypatch.setattr(jm.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(jm.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(jm.random, "uniform", lambda a, b: 0.0)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"ok")
    raw = buf.getvalue()

    attempts = {"n": 0}

    async def flaky_persist(name, data, ctype, job_id=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("transient")
        return {"name": name, "path": "/p"}

    monkeypatch.setattr(jm, "persist_file", flaky_persist)

    class F:
        filename = "z.zip"
        content_type = "application/zip"

        async def read(self):
            return raw

    out = await jm._process_one_upload(F(), job_id=1)
    assert out and out[0]["name"] == "a.txt"


@pytest.mark.asyncio
async def test_process_one_upload_zip_s3_stages_downloads_extracts_deletes_raw(monkeypatch):
    monkeypatch.setattr(jm.settings, "OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)
    monkeypatch.setattr(jm.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inner/doc.txt", b"zip s3")
    raw_zip = buf.getvalue()
    raw_entry = {"storage": "s3", "bucket": "b", "key": "z.zip"}
    deleted = []

    async def persist(name, data, ctype, job_id=None):
        if name == "bundle.zip":
            return raw_entry
        return {"name": name, "path": f"/{name}"}

    monkeypatch.setattr(jm, "persist_file", persist)
    monkeypatch.setattr(jm, "download_s3_bytes", lambda info: raw_zip)

    async def delf(entry):
        deleted.append(entry)

    monkeypatch.setattr(jm, "delete_file", delf)

    class F:
        filename = "bundle.zip"
        content_type = "application/zip"

        async def read(self):
            return raw_zip

    out = await jm._process_one_upload(F(), job_id=9)
    assert any(o["name"] == "doc.txt" for o in out)
    assert raw_entry in deleted


@pytest.mark.asyncio
async def test_process_one_upload_zip_s3_bad_zip_deletes_raw(monkeypatch):
    monkeypatch.setattr(jm.settings, "OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)
    monkeypatch.setattr(jm.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 1)
    raw_entry = {"storage": "s3", "bucket": "b", "key": "z.zip"}
    deleted = []

    async def persist(name, data, ctype, job_id=None):
        return raw_entry

    monkeypatch.setattr(jm, "persist_file", persist)
    monkeypatch.setattr(jm, "download_s3_bytes", lambda info: b"not-a-zip")

    async def delf(entry):
        deleted.append(entry)

    monkeypatch.setattr(jm, "delete_file", delf)

    class F:
        filename = "bad.zip"
        content_type = "application/zip"

        async def read(self):
            return b"raw"

    with pytest.raises(HTTPException) as e:
        await jm._process_one_upload(F(), job_id=1)
    assert e.value.status_code == 400
    assert raw_entry in deleted


@pytest.mark.asyncio
async def test_process_one_upload_zip_exhaust_retries_raises_500(monkeypatch):
    monkeypatch.setattr(jm.settings, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(jm.settings, "JOB_UPLOAD_MAX_FILE_BYTES", 1_000_000)
    monkeypatch.setattr(jm.settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(jm.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(jm.random, "uniform", lambda a, b: 0.0)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"x")
    raw = buf.getvalue()

    async def always_fail(*a, **k):
        raise OSError("persist always fails")

    monkeypatch.setattr(jm, "persist_file", always_fail)

    class F:
        filename = "z.zip"
        content_type = "application/zip"

        async def read(self):
            return raw

    with pytest.raises(HTTPException) as e:
        await jm._process_one_upload(F(), job_id=1)
    assert e.value.status_code == 500
