"""Unit tests for the JobSchedulerService (DateTrigger-based, one-time only).

Tests cover:
- Schedule execution callback triggers job correctly (IN_QUEUE → IN_PROGRESS)
- Only IN_QUEUE jobs are picked up; all other statuses are skipped
- Skips jobs without workflow steps, missing jobs/schedules
- Schedules deactivate and remove their APScheduler job after execution
- Execution history entries are created
- Thread failure sets job to FAILED (not APPROVED)
- Scheduler lifecycle (start/stop)
- add_schedule / update_schedule / remove_schedule wiring
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import Session

from core.security import get_password_hash
from models.agent import Agent
from models.job import (
    Job, JobSchedule, JobStatus, ScheduleStatus,
    WorkflowStep, ScheduleExecutionHistory,
)
from models.user import User, UserRole
from services.job_scheduler import (
    JobSchedulerService,
    _execute_schedule,
    reset_job_for_execution,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db: Session, role=UserRole.BUSINESS):
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"sched-{unique}@test.com",
        password_hash=get_password_hash("pass123"),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_agent(db: Session, developer: User):
    agent = Agent(
        developer_id=developer.id,
        name=f"Agent-{uuid.uuid4().hex[:6]}",
        pricing_model="pay_per_use",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="http://example.com/api",
        status="active",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def _make_job(db: Session, user: User, status=JobStatus.COMPLETED):
    job = Job(
        business_id=user.id,
        title="Scheduled Job",
        description="desc",
        status=status,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _make_step(db: Session, job: Job, agent: Agent):
    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="completed",
        cost=1.0,
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def _make_schedule(
    db: Session,
    job: Job,
    status=ScheduleStatus.ACTIVE,
    scheduled_at=None,
):
    if scheduled_at is None:
        scheduled_at = datetime.utcnow() + timedelta(hours=1)
    schedule = JobSchedule(
        job_id=job.id,
        status=status,
        timezone="UTC",
        scheduled_at=scheduled_at,
        next_run_time=scheduled_at if status == ScheduleStatus.ACTIVE else None,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


# ---------------------------------------------------------------------------
# _reset_job_for_execution
# ---------------------------------------------------------------------------

class TestResetJobForExecution:
    def test_resets_steps_and_job_fields(self, db_session):
        """reset_job_for_execution clears step data and job metadata.

        Note: it does NOT commit — the caller sets the final status and commits.
        """
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        step = _make_step(db_session, job, agent)
        step.output_data = '{"result": "done"}'
        step.status = "completed"
        db_session.commit()

        reset_job_for_execution(db_session, job)
        # Caller sets final status and commits
        job.status = JobStatus.IN_QUEUE
        db_session.commit()

        assert job.status == JobStatus.IN_QUEUE
        assert job.failure_reason is None
        db_session.refresh(step)
        assert step.status == "pending"
        assert step.output_data is None
        assert step.cost == 0.0


# ---------------------------------------------------------------------------
# _execute_schedule (the DateTrigger callback)
# ---------------------------------------------------------------------------

class TestExecuteSchedule:
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_triggers_execution_sets_in_progress(self, mock_session_local, mock_threading, db_session):
        """Schedule fires → IN_QUEUE job set to IN_PROGRESS, thread started."""
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.IN_QUEUE)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        _execute_schedule(schedule.id)

        assert job.status == JobStatus.IN_PROGRESS
        assert schedule.last_run_time is not None
        assert schedule.status == ScheduleStatus.INACTIVE
        assert schedule.next_run_time is None
        mock_thread.start.assert_called_once()

    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_creates_execution_history(self, mock_session_local, mock_threading, db_session):
        """History entry created when schedule fires."""
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.IN_QUEUE)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        _execute_schedule(schedule.id)

        history = db_session.query(ScheduleExecutionHistory).filter(
            ScheduleExecutionHistory.schedule_id == schedule.id
        ).all()
        assert len(history) == 1
        assert history[0].status == "started"
        assert history[0].triggered_by == "scheduler"

    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_skips_in_progress_job(self, mock_session_local, mock_threading, db_session):
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.IN_PROGRESS)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        _execute_schedule(schedule.id)

        assert job.status == JobStatus.IN_PROGRESS
        mock_threading.Thread.assert_not_called()

    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_skips_completed_job(self, mock_session_local, mock_threading, db_session):
        """Only IN_QUEUE jobs are picked up — COMPLETED jobs are skipped."""
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        _execute_schedule(schedule.id)

        assert job.status == JobStatus.COMPLETED
        mock_threading.Thread.assert_not_called()

    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_skip_records_history(self, mock_session_local, mock_threading, db_session):
        """Skipped executions should create a history entry with status='skipped'."""
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.IN_PROGRESS)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        _execute_schedule(schedule.id)

        history = db_session.query(ScheduleExecutionHistory).filter(
            ScheduleExecutionHistory.schedule_id == schedule.id
        ).all()
        assert len(history) == 1
        assert history[0].status == "skipped"

    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_skips_job_without_steps(self, mock_session_local, mock_threading, db_session):
        """IN_QUEUE job with no workflow steps is skipped."""
        user = _make_user(db_session)
        job = _make_job(db_session, user, JobStatus.IN_QUEUE)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        _execute_schedule(schedule.id)

        # Job stays IN_QUEUE — not executed because no steps
        assert job.status == JobStatus.IN_QUEUE
        mock_threading.Thread.assert_not_called()

    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_deactivates_and_removes(self, mock_session_local, mock_threading, mock_get_sched, db_session):
        """Schedule deactivates before thread starts, APScheduler job removed."""
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.IN_QUEUE)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread
        mock_svc = MagicMock()
        mock_get_sched.return_value = mock_svc

        _execute_schedule(schedule.id)

        assert schedule.status == ScheduleStatus.INACTIVE
        assert schedule.next_run_time is None
        assert schedule.last_run_time is not None
        mock_svc.remove_schedule.assert_called_once_with(schedule.id)

    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_thread_failure_sets_job_failed(self, mock_session_local, mock_threading, mock_get_sched, db_session):
        """If the thread fails to start, job should be set to FAILED (not APPROVED)."""
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.IN_QUEUE)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_thread = MagicMock()
        mock_thread.start.side_effect = RuntimeError("thread pool exhausted")
        mock_threading.Thread.return_value = mock_thread
        mock_svc = MagicMock()
        mock_get_sched.return_value = mock_svc

        _execute_schedule(schedule.id)

        assert job.status == JobStatus.FAILED
        assert "thread" in job.failure_reason.lower() or "failed" in job.failure_reason.lower()
        # History should also reflect the failure
        history = db_session.query(ScheduleExecutionHistory).filter(
            ScheduleExecutionHistory.schedule_id == schedule.id,
            ScheduleExecutionHistory.status == "failed",
        ).first()
        assert history is not None

    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.SessionLocal")
    def test_missing_schedule_removed(self, mock_session_local, mock_get_sched, db_session):
        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_svc = MagicMock()
        mock_get_sched.return_value = mock_svc

        _execute_schedule(99999)
        mock_svc.remove_schedule.assert_called_once_with(99999)


class TestRunJobInThreadGuards:
    @patch("services.job_scheduler.SessionLocal")
    @patch("services.job_scheduler.AgentExecutor")
    def test_skips_when_job_not_in_progress(self, mock_executor_cls, mock_session_local, db_session):
        user = _make_user(db_session)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        from services.job_scheduler import run_job_in_thread

        run_job_in_thread(job.id, history_id=None)
        mock_executor_cls.assert_not_called()

    @patch("services.job_scheduler.SessionLocal")
    @patch("services.job_scheduler.AgentExecutor")
    def test_skips_when_execution_token_mismatch(self, mock_executor_cls, mock_session_local, db_session):
        user = _make_user(db_session)
        job = _make_job(db_session, user, JobStatus.IN_PROGRESS)
        job.execution_token = "current-token-abc"
        db_session.commit()
        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        from services.job_scheduler import run_job_in_thread

        run_job_in_thread(job.id, history_id=None, execution_token="stale-token-xyz")
        mock_executor_cls.assert_not_called()


# ---------------------------------------------------------------------------
# JobSchedulerService lifecycle
# ---------------------------------------------------------------------------

class TestJobSchedulerServiceLifecycle:
    @patch("services.job_scheduler.SessionLocal")
    def test_start_and_stop(self, mock_session_local):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        assert not service.running

        service.start()
        assert service.running

        service.stop()
        assert not service.running

    @patch("services.job_scheduler.SessionLocal")
    def test_double_start_is_safe(self, mock_session_local):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()
        service.start()  # Should not raise
        assert service.running
        service.stop()

    def test_stop_without_start_is_safe(self):
        service = JobSchedulerService()
        service.stop()  # Should not raise
        assert not service.running


# ---------------------------------------------------------------------------
# add / update / remove schedule (DateTrigger-based)
# ---------------------------------------------------------------------------

class TestScheduleManagement:
    @patch("services.job_scheduler.SessionLocal")
    def test_add_schedule_registers_job(self, mock_session_local):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()

        future = datetime.utcnow() + timedelta(hours=1)
        service.add_schedule(1, scheduled_at=future, timezone="UTC")

        job = service._scheduler.get_job("schedule_1")
        assert job is not None
        service.stop()

    @patch("services.job_scheduler.SessionLocal")
    def test_update_schedule_reschedules(self, mock_session_local):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()

        future = datetime.utcnow() + timedelta(hours=1)
        service.add_schedule(1, scheduled_at=future, timezone="UTC")

        future2 = datetime.utcnow() + timedelta(hours=2)
        service.update_schedule(1, scheduled_at=future2, timezone="UTC")

        job = service._scheduler.get_job("schedule_1")
        assert job is not None
        service.stop()

    @patch("services.job_scheduler.SessionLocal")
    def test_remove_schedule_removes_job(self, mock_session_local):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()

        future = datetime.utcnow() + timedelta(hours=1)
        service.add_schedule(1, scheduled_at=future, timezone="UTC")
        service.remove_schedule(1)

        job = service._scheduler.get_job("schedule_1")
        assert job is None
        service.stop()

    def test_operations_when_not_running_are_safe(self):
        service = JobSchedulerService()
        future = datetime.utcnow() + timedelta(hours=1)
        # Should not raise even when scheduler is not running
        service.add_schedule(1, scheduled_at=future, timezone="UTC")
        service.update_schedule(1, scheduled_at=future, timezone="UTC")
        service.remove_schedule(1)
