"""Distributed scheduler service using Celery ETA tasks.

Each active JobSchedule is enqueued as a Celery ETA task with a stable task_id.
When a task fires, it resets the job's workflow steps and triggers execution.
Schedules are deactivated after execution (one-time only).

Routes call add_schedule / update_schedule / remove_schedule to keep Celery queue
in sync with the DB.  On startup, load_all_schedules() bootstraps from the DB
to ensure durability after restart.

Workflow (from the user's perspective):
  1. Before scheduled time — user can still edit the job.
  2. At scheduled time — job goes IN_QUEUE → IN_PROGRESS. No user actions.
  3. After completion:
     (a) Success — no further action items.
     (b) Failure — user can "Run Now" (POST /rerun) or "Schedule Again"
         (PUT /schedule with new scheduled_at).
"""

import asyncio
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

try:
    from db.database import SessionLocal
except ModuleNotFoundError:
    # Celery workers can start with a different import path; ensure backend root is importable.
    backend_root = os.path.dirname(os.path.dirname(__file__))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    from db.database import SessionLocal
from core.config import settings
from models.job import (
    Job, JobSchedule, JobStatus, ScheduleStatus,
    WorkflowStep, ScheduleExecutionHistory,
)
from services.agent_executor import AgentExecutor
from services.task_queue import enqueue_execute_platform_job

from services.task_queue import celery_app, trigger_scheduled_job

logger = logging.getLogger(__name__)

# Module-level singleton — set by JobSchedulerService.start()
_scheduler_service: Optional["JobSchedulerService"] = None


def get_scheduler() -> Optional["JobSchedulerService"]:
    """Return the running scheduler singleton (or None if disabled / not started)."""
    return _scheduler_service


# ---------------------------------------------------------------------------
# Job execution helpers (public API — used by routes for rerun)
# ---------------------------------------------------------------------------

def reset_job_for_execution(db: Session, job: Job):
    """Reset a job's workflow steps so it can be executed again.

    Clears step output/status/cost and resets job metadata.
    Does NOT commit — the caller is responsible for setting the final
    job status (e.g. IN_QUEUE) and committing in a single transaction
    to avoid a window where the job is in an intermediate state.
    """
    steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).all()
    for step in steps:
        step.output_data = None
        step.status = "pending"
        step.started_at = None
        step.completed_at = None
        step.cost = 0.0

    job.completed_at = None
    job.failure_reason = None


def run_job_in_thread(job_id: int, history_id: int = None, execution_token: Optional[str] = None):
    """Execute a job in a dedicated thread with its own DB session and event loop.

    The caller is responsible for setting the job status to IN_PROGRESS before
    spawning this thread. On success the job transitions to COMPLETED (handled
    by AgentExecutor). On failure the job is set to FAILED here so the user
    can choose "Run Now" or "Schedule Again" from the frontend.
    """
    db = SessionLocal()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.warning("Skipping execution for missing job_id=%s", job_id)
            if history_id:
                hist = db.query(ScheduleExecutionHistory).filter(
                    ScheduleExecutionHistory.id == history_id
                ).first()
                if hist:
                    hist.status = "failed"
                    hist.failure_reason = "Execution skipped: job not found"
                    hist.completed_at = datetime.utcnow()
                    db.commit()
            return
        if job.status != JobStatus.IN_PROGRESS:
            logger.warning(
                "Skipping duplicate/stale execution for job_id=%s with status=%s",
                job_id,
                job.status.value,
            )
            if history_id:
                hist = db.query(ScheduleExecutionHistory).filter(
                    ScheduleExecutionHistory.id == history_id
                ).first()
                if hist:
                    hist.status = "skipped"
                    hist.failure_reason = f"Execution skipped: job status is {job.status.value}"
                    hist.completed_at = datetime.utcnow()
                    db.commit()
            return
        if execution_token and getattr(job, "execution_token", None) != execution_token:
            logger.warning(
                "Skipping stale execution for job_id=%s token=%s current_token=%s",
                job_id,
                execution_token,
                getattr(job, "execution_token", None),
            )
            if history_id:
                hist = db.query(ScheduleExecutionHistory).filter(
                    ScheduleExecutionHistory.id == history_id
                ).first()
                if hist:
                    hist.status = "skipped"
                    hist.failure_reason = "Execution skipped: stale execution token"
                    hist.completed_at = datetime.utcnow()
                    db.commit()
            return
        executor = AgentExecutor(db)
        loop.run_until_complete(executor.execute_job(job_id))

        # Update execution history on success
        if history_id:
            hist = db.query(ScheduleExecutionHistory).filter(
                ScheduleExecutionHistory.id == history_id
            ).first()
            if hist:
                hist.status = "completed"
                hist.completed_at = datetime.utcnow()
                db.commit()
    except Exception as e:
        logger.exception("Job execution failed for job_id=%s", job_id)
        try:
            # Mark job as FAILED so it doesn't stay stuck in IN_PROGRESS.
            job = db.query(Job).filter(Job.id == job_id).first()
            if job and job.status == JobStatus.IN_PROGRESS:
                job.status = JobStatus.FAILED
                job.failure_reason = f"Execution failed: {str(e)[:450]}"
            # Update execution history on failure
            if history_id:
                hist = db.query(ScheduleExecutionHistory).filter(
                    ScheduleExecutionHistory.id == history_id
                ).first()
                if hist:
                    hist.status = "failed"
                    hist.failure_reason = f"Execution failed: {str(e)[:450]}"
                    hist.completed_at = datetime.utcnow()
            db.commit()
        except Exception:
            logger.exception("Failed to update job/history after failure for job_id=%s", job_id)
    finally:
        loop.close()
        db.close()


def queue_job_execution(
    job_id: int,
    history_id: int = None,
    execution_token: Optional[str] = None,
    *,
    strict: bool = False,
):
    """
    Queue-first execution: enqueue to Celery/Redis when enabled, else fallback to local thread.
    """
    if enqueue_execute_platform_job(
        job_id=job_id,
        history_id=history_id,
        execution_token=execution_token,
        strict=strict,
    ):
        logger.info("Queued job execution via Celery for job_id=%s history_id=%s", job_id, history_id)
        return
    thread = threading.Thread(target=run_job_in_thread, args=(job_id, history_id, execution_token), daemon=True)
    thread.start()


def _execute_schedule(schedule_id: int):
    """Callback fired by the Celery worker when an ETA task arrives.

    Opens its own DB session, validates the job, transitions it from
    IN_QUEUE → IN_PROGRESS, deactivates the schedule, and spawns an
    execution thread.

    Only jobs in IN_QUEUE can be picked up for execution. Jobs already
    IN_PROGRESS are skipped — they cannot be restarted.

    State machine:
      schedule fires → validate job is IN_QUEUE → reset steps
      → IN_PROGRESS → deactivate schedule → start thread
      → AgentExecutor runs → COMPLETED or FAILED
    """
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.id == schedule_id).first()
        if not schedule:
            logger.warning("Schedule %s no longer exists — revoking Celery ETA task", schedule_id)
            svc = get_scheduler()
            if svc:
                svc.remove_schedule(schedule_id)
            return

        if schedule.status != ScheduleStatus.ACTIVE:
            logger.info("Schedule %s is inactive — skipping", schedule_id)
            return

        job = db.query(Job).filter(Job.id == schedule.job_id).first()
        if not job:
            logger.warning("Schedule %s references missing job %s — deactivating", schedule.id, schedule.job_id)
            schedule.status = ScheduleStatus.INACTIVE
            schedule.next_run_time = None
            db.commit()
            svc = get_scheduler()
            if svc:
                svc.remove_schedule(schedule_id)
            return

        # Only IN_QUEUE jobs can be picked up for execution.
        # IN_PROGRESS jobs cannot be restarted; other statuses are unexpected.
        if job.status != JobStatus.IN_QUEUE:
            reason = (
                f"Job is {job.status.value} — cannot execute "
                f"(only jobs in in_queue state can be picked up)"
            )
            logger.warning(
                "Job %s is %s, expected in_queue — skipping schedule %s",
                job.id, job.status.value, schedule.id,
            )
            history = ScheduleExecutionHistory(
                schedule_id=schedule.id,
                job_id=job.id,
                status="skipped",
                failure_reason=reason,
                triggered_by="scheduler",
            )
            db.add(history)
            db.commit()
            return

        # Skip if job has no workflow steps (nothing to execute)
        step_count = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).count()
        if step_count == 0:
            logger.warning("Job %s has no workflow steps — skipping schedule %s", job.id, schedule.id)
            history = ScheduleExecutionHistory(
                schedule_id=schedule.id,
                job_id=job.id,
                status="skipped",
                failure_reason="Job has no workflow steps — add a workflow before scheduling",
                triggered_by="scheduler",
            )
            db.add(history)
            db.commit()
            return

        # Create execution history entry (audit log)
        history = ScheduleExecutionHistory(
            schedule_id=schedule.id,
            job_id=job.id,
            status="started",
            triggered_by="scheduler",
        )
        db.add(history)
        db.flush()  # Get history.id before thread start

        # Reset workflow steps for a clean execution
        reset_job_for_execution(db, job)

        # Transition: IN_QUEUE → IN_PROGRESS (execution is starting now)
        execution_token = f"sched-{schedule.id}-{uuid.uuid4().hex}"
        job.status = JobStatus.IN_PROGRESS
        job.execution_token = execution_token

        # Deactivate schedule before starting the thread (one-time schedule).
        # User can reschedule via PUT /schedule if the job fails.
        schedule.last_run_time = datetime.utcnow()
        schedule.status = ScheduleStatus.INACTIVE
        schedule.next_run_time = None
        db.commit()

        # Internal cleanup of the scheduler singleton state (task has already fired)
        svc = get_scheduler()
        if svc:
            svc.remove_schedule(schedule_id)

        # Queue execution (Celery first; thread fallback)
        logger.info("Triggering scheduled execution for job %s (schedule %s)", job.id, schedule.id)
        try:
            queue_job_execution(
                job.id,
                history.id,
                execution_token=execution_token,
                strict=bool(getattr(settings, "JOB_EXECUTION_STRICT_QUEUE", False)),
            )
        except Exception as exc:
            logger.exception("Failed to start execution thread for job %s", job.id)
            job.status = JobStatus.FAILED
            job.execution_token = None
            job.failure_reason = f"Scheduler failed to start execution: {str(exc)[:200]}"
            history.status = "failed"
            history.failure_reason = job.failure_reason
            history.completed_at = datetime.utcnow()
            db.commit()
    except Exception:
        logger.exception("Error executing schedule %s", schedule_id)
        db.rollback()
    finally:
        db.close()



# ---------------------------------------------------------------------------
# Celery helper
# ---------------------------------------------------------------------------


def _schedule_job_id(schedule_id: int) -> str:
    """Stable Celery task ID for a given schedule row (e.g. 'schedule_42')."""
    return f"schedule_{schedule_id}"


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class JobSchedulerService:
    """Manages scheduling lifecycle via Celery ETA tasks.

    Singleton — access via get_scheduler(). Started/stopped by the FastAPI
    lifespan handler in main.py. Disabled in tests via DISABLE_SCHEDULER=true.
    """

    def __init__(self):
        # BackgroundScheduler instance is gone we now use Celery ETA tasks, but we keep this for the stuck job watchdog and potential future in-process scheduling features.
        self._is_running = False
        self._scheduler = None

    @property
    def running(self) -> bool:
        # Since we moved to Celery ETA tasks, the scheduler is effectively always "running" as long as Celery is available and the API is running.
        return self._is_running

    # -- Lifecycle ----------------------------------------------------------

    def start(self):
        """Bootstrap: load all active schedules from the DB into Celery."""
        # 1. EARLY RETURN: Check if disabled before doing anything else
        if getattr(settings, "DISABLE_SCHEDULER", False):
            logger.info("Job scheduler is disabled via DISABLE_SCHEDULER setting.")
            return
        
        # 2. Set ONLY state if we are acctually starting the scheduler. This ensures that if there is an error during startup (e.g. DB connection issue), we won't have a half-started scheduler that could cause issues with the Celery tasks.
        self._is_running = True

        global _scheduler_service
        _scheduler_service = self

        # Bootstrap: load all active schedules from the DB
        self.load_all_schedules()
        logger.info("Job scheduler started (Celery ETA tasks loaded)")


    def stop(self):
        """Cleanup the singleton reference on shutdown. Celery ETA tasks are one-time and will be cleaned up automatically after firing, so no need to revoke them here."""
        global _scheduler_service
        _scheduler_service = None
        self._is_running = False
        logger.info("Job scheduler stopped")

    # -- Schedule management ------------------------------------------------

    def add_schedule(self, schedule_id: int, scheduled_at, timezone: str = "UTC"):
        """Register a new Celery ETA task for a schedule."""

        if not self._is_running: 
            return

        if not celery_app:
            return
        
        task_id = _schedule_job_id(schedule_id)

        try:
            # 1. Convert scheduled_at + timezone to a UTC datetime fot Celery ETA
            if scheduled_at.tzinfo is None:
                zone = ZoneInfo(timezone)
                scheduled_at = scheduled_at.replace(tzinfo=zone)

            run_at_utc = scheduled_at.astimezone(ZoneInfo("UTC"))

            # 2. Revoke any existing task just to be safe(idempotency)
            celery_app.control.revoke(task_id, terminate=True)

            # 3. apply the new ETA task
            trigger_scheduled_job.apply_async(
                args=[schedule_id],
                eta=run_at_utc,
                task_id=task_id,
            )
            logger.info("Added Celery ETA task %s (at: %s UTC)", task_id, timezone) 
        except Exception:
            logger.exception("Failed to add Celery ETA task for schedule %s", schedule_id)
        
    def update_schedule(self, schedule_id: int, scheduled_at, timezone: str = "UTC"):
        """Reschedule by simply overwriting the ETA task."""
        if not self._is_running: 
            return
        
        # Because we use a deterministic task_id based on schedule_id, add_schedule will revoke the old task and add the new one, effectively updating the schedule.
        self.add_schedule(schedule_id, scheduled_at, timezone) 

    def remove_schedule(self, schedule_id: int):
        """Remove an ETA task from the Celery queue."""
        if not self._is_running: 
            return
        
        if not celery_app:
            return
        
        task_id = _schedule_job_id(schedule_id)
        try:
            celery_app.control.revoke(task_id, terminate=True)
            logger.info("Removed Celery ETA task %s", task_id)
        except Exception:
            logger.exception("Failed to remove Celery ETA task for schedule_id %s", schedule_id)

    def load_all_schedules(self):
        """Load all active schedules from the DB and register DateTrigger jobs.

        Called once at startup. Schedules whose scheduled_at is in the past
        are deactivated automatically (they missed their window).
        """
        db = SessionLocal()
        try:
            active_schedules = (
                db.query(JobSchedule)
                .filter(JobSchedule.status == ScheduleStatus.ACTIVE)
                .all()
            )
            now = datetime.utcnow()
            loaded = 0
            for schedule in active_schedules:
                # Skip past-dated schedules — deactivate them
                sched_time = schedule.scheduled_at
                if sched_time and sched_time < now:
                    logger.info("Schedule %s is in the past (%s) — deactivating", schedule.id, sched_time)
                    schedule.status = ScheduleStatus.INACTIVE
                    schedule.next_run_time = None
                    db.commit()
                    continue

                self.add_schedule(
                    schedule.id,
                    scheduled_at=schedule.scheduled_at,
                    timezone=schedule.timezone or "UTC",
                )
                loaded += 1
            logger.info("Loaded %d active schedule(s) from DB", loaded)
        except Exception:
            logger.exception("Failed to load schedules from DB")
        finally:
            db.close()
