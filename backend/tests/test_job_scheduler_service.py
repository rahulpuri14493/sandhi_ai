"""Unit tests for the JobSchedulerService (Celery ETA-based, one-time only).

Tests cover:
- Schedule execution callback triggers job correctly (IN_QUEUE → IN_PROGRESS)
- Only IN_QUEUE jobs are picked up; all other statuses are skipped
- Skips jobs without workflow steps, missing jobs/schedules
- Schedules deactivate and clean up their task state after job execution
- Execution history entries are created
- Thread failure sets job to FAILED (not APPROVED)
- Scheduler lifecycle (start/stop)
- add_schedule / update_schedule / remove_schedule wiring
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

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

from core.config import settings

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
    @patch("services.job_scheduler.enqueue_execute_platform_job", return_value=False)
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_triggers_execution_sets_in_progress(self, mock_session_local, mock_threading, _mock_enqueue, db_session):
        """Schedule fires → IN_QUEUE job set to IN_PROGRESS, thread started."""

        # Save original settings to restore after test
        original_backend = settings.JOB_EXECUTION_BACKEND
        try:
            # Force fallback to local thread execution to test the core logic without Celery involved
            settings.JOB_EXECUTION_BACKEND = "local"

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
        finally:
            settings.JOB_EXECUTION_BACKEND = original_backend

    @patch("services.job_scheduler.enqueue_execute_platform_job", return_value=False)
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_creates_execution_history(self, mock_session_local, mock_threading, _mock_enqueue, db_session):
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

    @patch("services.job_scheduler.enqueue_execute_platform_job", return_value=False)
    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.threading")
    @patch("services.job_scheduler.SessionLocal")
    def test_deactivates_and_removes(self, mock_session_local, mock_threading, mock_get_sched, db_session):
        """Schedule deactivates before thread starts, task ID cleaned from service state."""
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

    def setup_method(self):
        """Ensure the scheduler is not disabled by global settings for these tests."""
        self._orig_disable = getattr(settings, "DISABLE_SCHEDULER", False)
        settings.DISABLE_SCHEDULER = False

    def teardown_method(self):
        """Restore the original setting."""
        settings.DISABLE_SCHEDULER = self._orig_disable

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
# add / update / remove schedule (Celery ETA-based)
# ---------------------------------------------------------------------------

class TestScheduleManagement:

    def setup_method(self):
        """Ensure the scheduler is not disabled by global settings for these tests."""
        self._orig_disable = getattr(settings, "DISABLE_SCHEDULER", False)
        settings.DISABLE_SCHEDULER = False

    def teardown_method(self):
        """Restore the original setting."""
        settings.DISABLE_SCHEDULER = self._orig_disable

    @patch("services.job_scheduler.trigger_scheduled_job")
    @patch("services.job_scheduler.celery_app")
    @patch("services.job_scheduler.SessionLocal")
    def test_add_schedule_registers_job(self, mock_session_local, mock_celery_app, mock_trigger):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()

        future = datetime.utcnow() + timedelta(hours=1)
        service.add_schedule(1, scheduled_at=future, timezone="UTC")

        # Verify Celery ETA task was queued
        mock_trigger.apply_async.assert_called_once()
        # Verify old task was revoked safely (idempotency check)
        mock_celery_app.control.revoke.assert_called_once_with("schedule_1", terminate=True)
        service.stop()

    @patch("services.job_scheduler.trigger_scheduled_job")
    @patch("services.job_scheduler.celery_app")
    @patch("services.job_scheduler.SessionLocal")
    def test_update_schedule_reschedules(self, mock_session_local, mock_celery_app, mock_trigger):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()

        future = datetime.utcnow() + timedelta(hours=1)
        service.add_schedule(1, scheduled_at=future, timezone="UTC")

        future2 = datetime.utcnow() + timedelta(hours=2)
        service.update_schedule(1, scheduled_at=future2, timezone="UTC")

        # Added once, updated once = 2 calls
        assert mock_trigger.apply_async.call_count == 2
        assert mock_celery_app.control.revoke.call_count == 2
        service.stop()

    @patch("services.job_scheduler.celery_app")
    @patch("services.job_scheduler.SessionLocal")
    def test_remove_schedule_removes_job(self, mock_session_local, mock_celery_app):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_session_local.return_value = mock_db

        service = JobSchedulerService()
        service.start()

        future = datetime.utcnow() + timedelta(hours=1)
        service.add_schedule(1, scheduled_at=future, timezone="UTC")
        service.remove_schedule(1)

        # 1 revoke from add_schedule safety check, 1 revoke from remove_schedule
        assert mock_celery_app.control.revoke.call_count == 2
        service.stop()

    @patch("services.job_scheduler.celery_app", new=None)
    def test_operations_when_not_running_are_safe(self):
        service = JobSchedulerService()
        future = datetime.utcnow() + timedelta(hours=1)
        # Should not raise even when celery_app is missing
        service.add_schedule(1, scheduled_at=future, timezone="UTC")
        service.update_schedule(1, scheduled_at=future, timezone="UTC")
        service.remove_schedule(1)


# ---------------------------------------------------------------------------
# Coverage Gap Fillers (Celery, Thread Execution, Bootstrap)
# ---------------------------------------------------------------------------

class TestSchedulerCoverageGaps:
    @patch("services.job_scheduler.JobSchedulerService.add_schedule")
    def test_load_all_schedules_past_and_future(self, mock_add, db_session):
        """Tests the bootstrap loop: deactivates past schedules, loads future ones."""
        user = _make_user(db_session)
        
        # 1. Past schedule (Needs its own job due to UNIQUE constraint)
        job_past = _make_job(db_session, user, JobStatus.IN_QUEUE)
        past_time = datetime.utcnow() - timedelta(hours=1)
        sched_past = _make_schedule(db_session, job_past, status=ScheduleStatus.ACTIVE, scheduled_at=past_time)
        sched_past_id = sched_past.id # Store ID for later
        
        # 2. Future schedule (Needs its own job)
        job_future = _make_job(db_session, user, JobStatus.IN_QUEUE)
        future_time = datetime.utcnow() + timedelta(hours=1)
        sched_future = _make_schedule(db_session, job_future, status=ScheduleStatus.ACTIVE, scheduled_at=future_time)

        service = JobSchedulerService()
        
        # Run the internal logic (which creates and closes its own session)
        with patch("services.job_scheduler.SessionLocal", return_value=db_session):
            service.load_all_schedules()

        # Re-query the past schedule safely by ID
        updated_past = db_session.query(JobSchedule).filter_by(id=sched_past_id).first()
        
        assert updated_past.status == ScheduleStatus.INACTIVE
        assert updated_past.next_run_time is None

        # Verify future was loaded into Celery
        mock_add.assert_any_call(sched_future.id, scheduled_at=future_time, timezone="UTC")


    @patch("services.job_scheduler.trigger_scheduled_job")
    @patch("services.job_scheduler.celery_app")
    def test_add_schedule_naive_datetime_tz_conversion(self, mock_app, mock_trigger):
        """Tests the timezone conversion block in add_schedule."""
        service = JobSchedulerService()
        service._is_running = True # Manually set running state to test the logic without starting the full scheduler
        naive_dt = datetime.utcnow() # No tzinfo
        
        service.add_schedule(1, scheduled_at=naive_dt, timezone="America/New_York")
        mock_trigger.apply_async.assert_called_once()

    @patch("services.job_scheduler.enqueue_execute_platform_job", return_value=True)
    @patch("services.job_scheduler.threading.Thread")
    def test_queue_job_execution_celery_path(self, mock_thread, mock_enqueue):
        """Tests that the thread fallback is skipped if Celery enqueue succeeds."""
        from services.job_scheduler import queue_job_execution
        queue_job_execution(1)
        
        mock_enqueue.assert_called_once()
        mock_thread.assert_not_called()

    @patch("services.job_scheduler.AgentExecutor")
    def test_run_job_in_thread_success_and_failure(self, mock_executor_cls, db_session):
        """Tests the actual execution block and exception handling."""
        from unittest.mock import AsyncMock
        
        user = _make_user(db_session)
        job = _make_job(db_session, user, JobStatus.IN_PROGRESS)
        hist = ScheduleExecutionHistory(schedule_id=1, job_id=job.id, status="started")
        db_session.add(hist)
        db_session.commit()
        
        job_id = job.id
        hist_id = hist.id

        # mock the async execution
        mock_executor = MagicMock()
        mock_executor.execute_job = AsyncMock()
        mock_executor_cls.return_value = mock_executor
        
        from services.job_scheduler import run_job_in_thread

        # 1. Test Success Path
        with patch("services.job_scheduler.SessionLocal", return_value=db_session):
            run_job_in_thread(job_id, history_id=hist_id)
            
        # Re-query safely by ID
        hist_updated = db_session.query(ScheduleExecutionHistory).filter_by(id=hist_id).first()
        assert hist_updated.status == "completed"
        
        # 2. Test Failure Path
        # Re-query job to reset state safely
        job = db_session.query(Job).filter_by(id=job_id).first()
        job.status = JobStatus.IN_PROGRESS
        db_session.commit()
        
        # Make the async mock raise an exception
        mock_executor.execute_job.side_effect = Exception("Simulated Executor Crash")
        
        with patch("services.job_scheduler.SessionLocal", return_value=db_session):
            run_job_in_thread(job_id, history_id=hist_id)
            
        job_failed = db_session.query(Job).filter_by(id=job_id).first()
        hist_failed = db_session.query(ScheduleExecutionHistory).filter_by(id=hist_id).first()
        
        assert job_failed.status == JobStatus.FAILED
        assert "Simulated Executor Crash" in job_failed.failure_reason
        assert hist_failed.status == "failed"

    @patch("services.job_scheduler.celery_app")
    def test_add_schedule_exception_handling(self, mock_app):
        """Triggers the 'except Exception' block in add_schedule (Lines 380-382)."""
        service = JobSchedulerService()
        service._is_running = True # Manually set running state to test the logic without starting the full scheduler
        mock_app.control.revoke.side_effect = Exception("Celery Connection Refused")
        
        # This should not raise an error, but trigger the logger.exception
        service.add_schedule(1, scheduled_at=datetime.utcnow())

    @patch("services.job_scheduler.celery_app")
    def test_remove_schedule_exception_handling(self, mock_app):
        """Triggers the 'except Exception' block in remove_schedule (Lines 437-438)."""
        service = JobSchedulerService()
        service._is_running = True # Manually set running state to test the logic without starting the full scheduler
        mock_app.control.revoke.side_effect = Exception("Celery Revoke Failed")
        
        # This should trigger the logger.exception
        service.remove_schedule(1)

    @patch("services.job_scheduler.SessionLocal")
    def test_load_all_schedules_exception_handling(self, mock_session_local):
        """Triggers the 'except Exception' block in load_all_schedules (Lines 472-473)."""
        service = JobSchedulerService()
        
        # Create a fake DB session
        mock_db = MagicMock()
        # Make the DB crash when it tries to run the query inside the 'try' block
        mock_db.query.side_effect = Exception("DB Query Failed")
        mock_session_local.return_value = mock_db
        
        # This will now hit the try block, crash on .query(), and successfully hit the except block
        service.load_all_schedules()

    @patch("services.job_scheduler.get_scheduler")
    @patch("services.job_scheduler.SessionLocal")
    def test_execute_schedule_missing_job_branch(self, mock_session_local, mock_get_sched):
        """Verify that orphan schedules are automatically deactivated and cleaned up if the associated job is missing."""
        mock_db = MagicMock()
        # Mock finding the schedule but NOT finding the job
        mock_sched = MagicMock(id=1, job_id=999, status=ScheduleStatus.ACTIVE)
        mock_db.query.return_value.filter.return_value.first.side_effect = [mock_sched, None]
        mock_session_local.return_value = mock_db
        
        from services.job_scheduler import _execute_schedule
        _execute_schedule(1)
        assert mock_sched.status == ScheduleStatus.INACTIVE

    @patch("services.job_scheduler.celery_app")
    def test_add_schedule_exception_path(self, mock_app):
        """Verify that the service handles external queue failures gracefully without crashing."""
        service = JobSchedulerService()
        service._is_running = True # Manually set running state to test the logic without starting the full scheduler
        mock_app.control.revoke.side_effect = Exception("Redis Down")
        # Should trigger the except block and logger
        service.add_schedule(1, scheduled_at=datetime.utcnow())