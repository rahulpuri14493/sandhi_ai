"""Unit tests for planner_artifact_storage (persist + read corner cases)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from models.job import Job, JobPlannerArtifact, JobStatus
from models.user import User, UserRole
from core.security import get_password_hash
from services.planner_artifact_storage import (
    GENERIC_PLANNER_ARTIFACT_TYPES,
    PLANNER_PIPELINE_ARTIFACT_TYPES,
    _should_persist_brd_payload,
    attach_planner_meta,
    load_latest_planner_pipeline_payloads,
    persist_brd_analysis_artifact,
    persist_json_planner_artifact,
    read_planner_artifact_bytes,
)


@pytest.mark.asyncio
async def test_persist_json_rejects_unknown_artifact_type(db_session):
    u = User(
        email="stor_rej@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    out = await persist_json_planner_artifact(db_session, job.id, "not_a_real_type", {"a": 1})
    assert out is None


@pytest.mark.asyncio
async def test_persist_json_success_inserts_row(db_session, tmp_path, monkeypatch):
    u = User(
        email="stor_u1@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)

    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_persist(name, data, content_type, job_id=None):
        dest = tmp_path / name
        dest.write_bytes(data)
        return {"storage": "local", "path": str(dest)}

    import services.planner_artifact_storage as pas

    monkeypatch.setattr(pas, "persist_file", fake_persist)

    aid = await persist_json_planner_artifact(
        db_session,
        job.id,
        "task_split",
        {"raw_llm_response": "x", "parsed_assignments": []},
    )
    assert aid is not None
    db_session.commit()
    row = db_session.query(JobPlannerArtifact).filter(JobPlannerArtifact.id == aid).first()
    assert row is not None
    assert row.artifact_type == "task_split"
    assert row.job_id == job.id
    raw = read_planner_artifact_bytes(row)
    body = json.loads(raw.decode())
    assert body["raw_llm_response"] == "x"
    assert "planner_meta" in body
    assert body["planner_meta"]["schema_version"] == "planner_artifact.v1"
    assert body["planner_meta"]["artifact_type"] == "task_split"


def test_should_persist_brd_payload_false_for_extraction_stub():
    assert _should_persist_brd_payload(
        {
            "analysis": "Document text extracted for job 'X'. Select and assign agents to this job to enable AI-powered analysis and Q&A.",
            "questions": [],
            "raw_response": "",
        }
    ) is False


def test_should_persist_brd_payload_true_when_questions():
    assert _should_persist_brd_payload(
        {
            "analysis": "Real analysis here.",
            "questions": ["Q1?"],
            "recommendations": [],
        }
    ) is True


@pytest.mark.asyncio
async def test_persist_brd_returns_none_when_should_skip(db_session):
    u = User(
        email="stor_brd@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    out = await persist_brd_analysis_artifact(
        db_session,
        job.id,
        {"analysis": "Document text extracted for job 'X'. Select and assign agents", "questions": []},
    )
    assert out is None


@pytest.mark.asyncio
async def test_persist_row_returns_none_when_persist_file_raises(db_session, monkeypatch):
    u = User(
        email="stor_raise@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def boom(*a, **k):
        raise RuntimeError("disk full")

    import services.planner_artifact_storage as pas

    monkeypatch.setattr(pas, "persist_file", boom)

    out = await persist_json_planner_artifact(db_session, job.id, "tool_suggestion", {"x": 1})
    assert out is None


@pytest.mark.asyncio
async def test_persist_row_returns_none_when_local_path_missing(db_session, monkeypatch):
    u = User(
        email="stor_bad@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def bad_meta(*a, **k):
        return {"storage": "local", "path": ""}

    import services.planner_artifact_storage as pas

    monkeypatch.setattr(pas, "persist_file", bad_meta)

    out = await persist_json_planner_artifact(db_session, job.id, "task_split", {"a": 1})
    assert out is None


def test_generic_artifact_types_frozen():
    assert "task_split" in GENERIC_PLANNER_ARTIFACT_TYPES
    assert "tool_suggestion" in GENERIC_PLANNER_ARTIFACT_TYPES
    assert "brd_analysis" not in GENERIC_PLANNER_ARTIFACT_TYPES


def test_read_planner_artifact_bytes_s3_delegates(monkeypatch):
    row = MagicMock()
    row.storage = "s3"
    row.bucket = "b"
    row.object_key = "k"

    monkeypatch.setattr(
        "services.planner_artifact_storage.download_s3_bytes",
        lambda meta: b'{"s3":true}',
    )
    assert read_planner_artifact_bytes(row) == b'{"s3":true}'


def test_attach_planner_meta_preserves_keys_and_sets_envelope(monkeypatch):
    monkeypatch.setattr(
        "services.planner_artifact_storage.is_agent_planner_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "services.planner_artifact_storage.settings",
        SimpleNamespace(AGENT_PLANNER_MODEL="  test-model  "),
    )
    out = attach_planner_meta({"foo": 1, "nested": {"a": 2}}, "task_split")
    assert out["foo"] == 1
    assert out["nested"]["a"] == 2
    meta = out["planner_meta"]
    assert meta["schema_version"] == "planner_artifact.v1"
    assert meta["artifact_type"] == "task_split"
    assert meta["planner_model"] == "test-model"
    assert "created_at" in meta


def test_attach_planner_meta_uses_planner_disabled_when_not_enabled(monkeypatch):
    monkeypatch.setattr(
        "services.planner_artifact_storage.is_agent_planner_configured",
        lambda: False,
    )
    out = attach_planner_meta({"x": 1}, "brd_analysis")
    assert out["planner_meta"]["planner_model"] == "planner_disabled"


def test_attach_planner_meta_delegates_to_is_agent_planner_configured(monkeypatch):
    """attach_planner_meta uses planner_llm.is_agent_planner_configured (transport-aware)."""
    import services.planner_llm as plm

    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_ENABLED", True)
    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_API_KEY", "  k  ")
    # Ensure secondary planner config does not leak from the developer environment.
    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_SECONDARY_ENABLED", False)
    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_SECONDARY_API_KEY", "   ")
    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_A2A_URL", "")
    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_ADAPTER_URL", "")
    from services.planner_llm import is_agent_planner_configured

    assert is_agent_planner_configured() is True

    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_API_KEY", "   ")
    assert is_agent_planner_configured() is False

    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_API_KEY", "k")
    monkeypatch.setattr(plm.settings, "AGENT_PLANNER_ENABLED", False)
    assert is_agent_planner_configured() is False


def test_planner_pipeline_artifact_types_order():
    assert PLANNER_PIPELINE_ARTIFACT_TYPES == ("brd_analysis", "task_split", "tool_suggestion")


def test_load_latest_planner_pipeline_payloads_empty_job(db_session):
    u = User(
        email="pipe_empty@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    payloads, row_ids = load_latest_planner_pipeline_payloads(db_session, job.id)
    for k in PLANNER_PIPELINE_ARTIFACT_TYPES:
        assert payloads[k] is None
        assert row_ids[k] is None


def test_load_latest_planner_pipeline_invalid_json_keeps_row_id(db_session, tmp_path):
    from datetime import datetime

    u = User(
        email="pipe_bad@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    bad = tmp_path / "bad.json"
    raw_invalid = "not json {{{"
    bad.write_text(raw_invalid, encoding="utf-8")
    ts = datetime(2024, 6, 1, 12, 0, 0)
    art = JobPlannerArtifact(
        job_id=job.id,
        artifact_type="task_split",
        storage="local",
        bucket=None,
        object_key=str(bad),
        byte_size=len(raw_invalid.encode("utf-8")),
        created_at=ts,
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    payloads, row_ids = load_latest_planner_pipeline_payloads(db_session, job.id)
    assert payloads["task_split"] is None
    assert row_ids["task_split"] == art.id


def test_load_latest_planner_pipeline_id_tiebreak_when_same_created_at(db_session, tmp_path):
    from datetime import datetime

    u = User(
        email="pipe_tie@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    ts = datetime(2024, 6, 1, 12, 0, 0)
    f_old = tmp_path / "o.json"
    f_old.write_text('{"v": "older"}', encoding="utf-8")
    f_new = tmp_path / "n.json"
    f_new.write_text('{"v": "newer"}', encoding="utf-8")

    db_session.add_all(
        [
            JobPlannerArtifact(
                job_id=job.id,
                artifact_type="tool_suggestion",
                storage="local",
                bucket=None,
                object_key=str(f_old),
                byte_size=1,
                created_at=ts,
            ),
            JobPlannerArtifact(
                job_id=job.id,
                artifact_type="tool_suggestion",
                storage="local",
                bucket=None,
                object_key=str(f_new),
                byte_size=1,
                created_at=ts,
            ),
        ]
    )
    db_session.commit()

    payloads, row_ids = load_latest_planner_pipeline_payloads(db_session, job.id)
    assert payloads["tool_suggestion"] == {"v": "newer"}
    assert row_ids["tool_suggestion"] is not None


@pytest.mark.asyncio
async def test_persist_brd_success_wraps_planner_meta(db_session, tmp_path, monkeypatch):
    u = User(
        email="stor_brd_ok@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    job = Job(business_id=u.id, title="J", status=JobStatus.DRAFT, conversation=json.dumps([]))
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    async def fake_persist(name, data, content_type, job_id=None):
        dest = tmp_path / name
        dest.write_bytes(data)
        return {"storage": "local", "path": str(dest)}

    import services.planner_artifact_storage as pas

    monkeypatch.setattr(pas, "persist_file", fake_persist)

    aid = await persist_brd_analysis_artifact(
        db_session,
        job.id,
        {
            "analysis": "Real BRD content.",
            "questions": ["One?"],
            "raw_response": "{}",
        },
    )
    assert aid is not None
    db_session.commit()
    row = db_session.query(JobPlannerArtifact).filter(JobPlannerArtifact.id == aid).first()
    assert row.artifact_type == "brd_analysis"
    body = json.loads(read_planner_artifact_bytes(row).decode())
    assert body["analysis"] == "Real BRD content."
    assert body["planner_meta"]["artifact_type"] == "brd_analysis"
    assert body["planner_meta"]["schema_version"] == "planner_artifact.v1"
