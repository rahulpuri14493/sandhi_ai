"""Unit tests for planner_artifact_storage (persist + read corner cases)."""

import json
from unittest.mock import MagicMock

import pytest

from models.job import Job, JobPlannerArtifact, JobStatus
from models.user import User, UserRole
from core.security import get_password_hash
from services.planner_artifact_storage import (
    GENERIC_PLANNER_ARTIFACT_TYPES,
    _should_persist_brd_payload,
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
    assert json.loads(raw.decode())["raw_llm_response"] == "x"


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
