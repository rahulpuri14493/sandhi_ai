from datetime import datetime, timedelta
import uuid

from core.security import get_password_hash
from models.agent import Agent
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole
from services import task_queue as tq


def _seed_step(db_session, *, old: bool) -> WorkflowStep:
    suffix = "old" if old else "new"
    uniq = uuid.uuid4().hex[:8]
    business = User(
        email=f"hb-retention-biz-{suffix}-{uniq}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.BUSINESS,
    )
    dev = User(
        email=f"hb-retention-dev-{suffix}-{uniq}@example.com",
        password_hash=get_password_hash("pw123456"),
        role=UserRole.DEVELOPER,
    )
    db_session.add_all([business, dev])
    db_session.commit()
    db_session.refresh(business)
    db_session.refresh(dev)

    agent = Agent(
        developer_id=dev.id,
        name=f"Retention Agent {suffix}",
        description="Retention",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://agent.example.com",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    now = datetime.utcnow()
    t = now - timedelta(days=40) if old else now - timedelta(days=1)
    job = Job(
        business_id=business.id,
        title=f"Retention job {suffix}",
        description="",
        status=JobStatus.COMPLETED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="completed",
        started_at=t,
        completed_at=t,
        live_phase="calling_tool",
        live_phase_started_at=t,
        live_reason_code="tool_call_started",
        live_reason_detail='{"tool_name":"platform_1_db"}',
        live_trace_id="trace-old" if old else "trace-new",
        live_attempt=1,
        last_activity_at=t,
        last_progress_at=t,
        stuck_since=t,
        stuck_reason="old stuck",
    )
    db_session.add(step)
    db_session.commit()
    db_session.refresh(step)
    return step


def test_heartbeat_retention_cleanup_clears_old_steps(monkeypatch, db_session):
    old_step = _seed_step(db_session, old=True)
    old_step_id = old_step.id
    _new_step = _seed_step(db_session, old=False)

    monkeypatch.setattr(tq.settings, "HEARTBEAT_RETENTION_DAYS", 30)

    class _Local:
        def __call__(self):
            return db_session

    monkeypatch.setattr(tq, "cleanup_heartbeat_retention_once", tq.cleanup_heartbeat_retention_once)
    monkeypatch.setattr("services.task_queue.SessionLocal", _Local(), raising=False)

    # Patch via closure: cleanup function imports SessionLocal lazily from db.database.
    import db.database as dbmod

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: db_session)
    result = tq.cleanup_heartbeat_retention_once()
    assert result["cleared_steps"] >= 1

    refreshed = db_session.query(WorkflowStep).filter(WorkflowStep.id == old_step_id).first()
    assert refreshed.live_phase is None
    assert refreshed.live_reason_code is None
    assert refreshed.live_reason_detail is None
    assert refreshed.live_trace_id is None
    assert refreshed.stuck_reason is None


def test_heartbeat_retention_cleanup_keeps_recent_steps(monkeypatch, db_session):
    step = _seed_step(db_session, old=False)
    step_id = step.id
    monkeypatch.setattr(tq.settings, "HEARTBEAT_RETENTION_DAYS", 30)
    import db.database as dbmod

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: db_session)
    tq.cleanup_heartbeat_retention_once()

    refreshed = db_session.query(WorkflowStep).filter(WorkflowStep.id == step_id).first()
    assert refreshed.live_phase is not None
    assert refreshed.live_reason_code is not None


def test_heartbeat_retention_cleanup_keeps_active_step_even_if_phase_started_old(monkeypatch, db_session):
    step = _seed_step(db_session, old=True)
    step_id = step.id
    step.status = "in_progress"
    step.last_activity_at = datetime.utcnow() - timedelta(minutes=3)
    step.last_progress_at = datetime.utcnow() - timedelta(minutes=2)
    db_session.commit()

    monkeypatch.setattr(tq.settings, "HEARTBEAT_RETENTION_DAYS", 30)
    import db.database as dbmod

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: db_session)
    tq.cleanup_heartbeat_retention_once()

    refreshed = db_session.query(WorkflowStep).filter(WorkflowStep.id == step_id).first()
    assert refreshed.live_phase is not None
    assert refreshed.live_reason_code is not None
