"""Unit tests for the JobSchedulerService (CronTrigger-based).

Tests cover:
- Schedule execution callback triggers job correctly
- Skips in-progress jobs, jobs without steps, missing jobs
- One-time schedules deactivate and remove their APScheduler job
- Recurring schedules update next_run_time
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
from models.job import Job, JobSchedule, JobStatus, ScheduleStatus, WorkflowStep
from models.user import User, UserRole
from services.job_scheduler import (
    JobSchedulerService,
    _execute_schedule,
    _reset_job_for_execution,
    _cron_to_trigger,
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
    cron: str = "0 2 * * *",
    status=ScheduleStatus.ACTIVE,
    is_one_time: bool = False,
):
    schedule = JobSchedule(
        job_id=job.id,
        cron_expression=cron,
        status=status,
        is_one_time=is_one_time,
        next_run_time=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


# ---------------------------------------------------------------------------
# _cron_to_trigger
# ---------------------------------------------------------------------------

class TestCronToTrigger:
    def test_parses_standard_cron(self):
        trigger = _cron_to_trigger("0 9 * * 1-5")
        assert trigger is not None

    def test_rejects_wrong_field_count(self):
        with pytest.raises(ValueError):
            _cron_to_trigger("0 9 * *")


# ---------------------------------------------------------------------------
# _reset_job_for_execution
# ---------------------------------------------------------------------------

class TestResetJobForExecution:
    def test_resets_steps_and_job_status(self, db_session):
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        step = _make_step(db_session, job, agent)
        step.output_data = '{"result": "done"}'
        step.status = "completed"
        db_session.commit()

        _reset_job_for_execution(db_session, job)

        assert job.status == JobStatus.PENDING_APPROVAL
        assert job.failure_reason is None
        db_session.refresh(step)
        assert step.status == "pending"
        assert step.output_data is None
        assert step.cost == 0.0


# ---------------------------------------------------------------------------
# _execute_schedule (the CronTrigger callback)
# ---------------------------------------------------------------------------

class TestExecuteSchedule:
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_triggers_execution(self, mock_session_local, mock_threading, db_session):
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        _execute_schedule(schedule.id)

        assert job.status == JobStatus.IN_PROGRESS
        assert schedule.last_run_time is not None
        assert schedule.next_run_time is not None  # Recurring: recomputed
        mock_thread.start.assert_called_once()

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
    def test_skips_job_without_steps(self, mock_session_local, mock_threading, db_session):
        user = _make_user(db_session)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        schedule = _make_schedule(db_session, job)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None

        _execute_schedule(schedule.id)

        assert job.status == JobStatus.COMPLETED
        mock_threading.Thread.assert_not_called()

    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_one_time_deactivates_and_removes(self, mock_session_local, mock_threading, mock_get_sched, db_session):
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job, is_one_time=True)

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

    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_recurring_advances_next_run(self, mock_session_local, mock_threading, db_session):
        user = _make_user(db_session)
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        job = _make_job(db_session, user, JobStatus.COMPLETED)
        _make_step(db_session, job, agent)
        schedule = _make_schedule(db_session, job, is_one_time=False)

        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        _execute_schedule(schedule.id)

        assert schedule.status == ScheduleStatus.ACTIVE
        assert schedule.next_run_time is not None
        assert schedule.next_run_time > datetime.utcnow() - timedelta(minutes=1)

    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.SessionLocal")
    def test_missing_schedule_removed(self, mock_session_local, mock_get_sched, db_session):
        mock_session_local.return_value = db_session
        db_session.close = lambda: None
        mock_svc = MagicMock()
        mock_get_sched.return_value = mock_svc

        _execute_schedule(99999)
        mock_svc.remove_schedule.assert_called_once_with(99999)


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
# add / update / remove schedule
# ---------------------------------------------------------------------------

class TestScheduleManagement:
    @patch("services.job_scheduler.SessionLocal")
    def test_add_schedule_registers_job(self, mock_session_local):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()
        service.add_schedule(1, "0 9 * * *")

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
        service.add_schedule(1, "0 9 * * *")
        service.update_schedule(1, "30 14 * * *")

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
        service.add_schedule(1, "0 9 * * *")
        service.remove_schedule(1)

        job = service._scheduler.get_job("schedule_1")
        assert job is None
        service.stop()

    def test_operations_when_not_running_are_safe(self):
        service = JobSchedulerService()
        # Should not raise even when scheduler is not running
        service.add_schedule(1, "0 9 * * *")
        service.update_schedule(1, "30 14 * * *")
        service.remove_schedule(1)
