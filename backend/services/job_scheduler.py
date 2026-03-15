"""Background scheduler service using APScheduler CronTriggers.

Each active JobSchedule gets its own APScheduler CronTrigger job — no polling.
When a schedule fires, it resets the job's workflow steps and triggers execution.

Routes call add_schedule / update_schedule / remove_schedule to keep APScheduler
in sync with the DB.  On startup, load_all_schedules() bootstraps from the DB.
"""

import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from croniter import croniter
from sqlalchemy.orm import Session

from db.database import SessionLocal
from models.job import Job, JobSchedule, JobStatus, ScheduleStatus, WorkflowStep
from services.agent_executor import AgentExecutor

logger = logging.getLogger(__name__)

# Module-level singleton — set by JobSchedulerService.start()
_scheduler_service: Optional["JobSchedulerService"] = None


def get_scheduler() -> Optional["JobSchedulerService"]:
    """Return the running scheduler singleton (or None if disabled / not started)."""
    return _scheduler_service


# ---------------------------------------------------------------------------
# Job execution helpers
# ---------------------------------------------------------------------------

def _reset_job_for_execution(db: Session, job: Job):
    """Reset a job's workflow steps so it can be executed again."""
    steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).all()
    for step in steps:
        step.output_data = None
        step.status = "pending"
        step.started_at = None
        step.completed_at = None
        step.cost = 0.0

    job.status = JobStatus.PENDING_APPROVAL
    job.completed_at = None
    job.failure_reason = None
    db.commit()
    db.refresh(job)


def _run_job_in_thread(job_id: int):
    """Execute a job in a dedicated thread with its own DB session and event loop."""
    db = SessionLocal()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        executor = AgentExecutor(db)
        loop.run_until_complete(executor.execute_job(job_id))
    except Exception:
        logger.exception("Scheduled job execution failed for job_id=%s", job_id)
    finally:
        loop.close()
        db.close()


def _execute_schedule(schedule_id: int):
    """Callback fired by APScheduler when a CronTrigger fires.

    Opens its own DB session, validates the job, resets it, and triggers execution.
    One-time schedules are deactivated and their APScheduler job is removed.
    """
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.id == schedule_id).first()
        if not schedule:
            logger.warning("Schedule %s no longer exists — removing APScheduler job", schedule_id)
            svc = get_scheduler()
            if svc:
                svc.remove_schedule(schedule_id)
            return

        if schedule.status != ScheduleStatus.ACTIVE:
            logger.info("Schedule %s is inactive — skipping", schedule_id)
            return

        job = db.query(Job).filter(Job.id == schedule.job_id).first()
        if not job:
            logger.warning("Schedule %s references missing job %s — skipping", schedule.id, schedule.job_id)
            return

        # Skip if already running
        if job.status == JobStatus.IN_PROGRESS:
            logger.warning("Job %s is already IN_PROGRESS — skipping schedule %s", job.id, schedule.id)
            return

        # Skip if job has no workflow steps
        step_count = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).count()
        if step_count == 0:
            logger.warning("Job %s has no workflow steps — skipping schedule %s", job.id, schedule.id)
            return

        # Reset and execute
        _reset_job_for_execution(db, job)
        job.status = JobStatus.IN_PROGRESS
        db.commit()

        logger.info("Triggering scheduled execution for job %s (schedule %s)", job.id, schedule.id)
        thread = threading.Thread(target=_run_job_in_thread, args=(job.id,), daemon=True)
        thread.start()

        # Update schedule metadata
        schedule.last_run_time = datetime.utcnow()
        if schedule.is_one_time:
            schedule.status = ScheduleStatus.INACTIVE
            schedule.next_run_time = None
            db.commit()
            logger.info("One-time schedule %s deactivated after execution", schedule.id)
            # Remove from APScheduler
            svc = get_scheduler()
            if svc:
                svc.remove_schedule(schedule_id)
        else:
            schedule.next_run_time = croniter(schedule.cron_expression, datetime.utcnow()).get_next(datetime)
            db.commit()
            logger.info("Recurring schedule %s next run at %s", schedule.id, schedule.next_run_time)
    except Exception:
        logger.exception("Error executing schedule %s", schedule_id)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cron expression → APScheduler CronTrigger
# ---------------------------------------------------------------------------

def _cron_to_trigger(cron_expression: str, timezone: str = "UTC") -> CronTrigger:
    """Convert a 5-field cron expression to an APScheduler CronTrigger."""
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron, got {len(parts)}: {cron_expression}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=ZoneInfo(timezone),
    )


def _datetime_to_date_trigger(scheduled_at, timezone: str = "UTC") -> DateTrigger:
    """Create a DateTrigger for a one-time schedule."""
    return DateTrigger(run_date=scheduled_at, timezone=ZoneInfo(timezone))


def _schedule_job_id(schedule_id: int) -> str:
    """APScheduler job ID for a given schedule row."""
    return f"schedule_{schedule_id}"


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class JobSchedulerService:
    """Manages APScheduler lifecycle with per-schedule CronTrigger jobs."""

    def __init__(self):
        self._scheduler: BackgroundScheduler | None = None

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Start the scheduler and load all active schedules from the DB."""
        global _scheduler_service

        if self._scheduler and self._scheduler.running:
            logger.warning("Scheduler is already running")
            return

        self._scheduler = BackgroundScheduler()
        self._scheduler.start()
        _scheduler_service = self

        # Bootstrap: load all active schedules from the DB
        self.load_all_schedules()
        logger.info("Job scheduler started (CronTrigger per schedule)")

    def stop(self):
        global _scheduler_service
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Job scheduler stopped")
        self._scheduler = None
        _scheduler_service = None

    # ── Schedule management ───────────────────────────────────────────────

    def add_schedule(self, schedule_id: int, cron_expression: str, timezone: str = "UTC", scheduled_at=None, is_one_time: bool = False):
        """Register a new trigger job for a schedule (CronTrigger or DateTrigger)."""
        if not self._scheduler or not self._scheduler.running:
            return
        job_id = _schedule_job_id(schedule_id)
        try:
            if is_one_time and scheduled_at:
                trigger = _datetime_to_date_trigger(scheduled_at, timezone)
            else:
                trigger = _cron_to_trigger(cron_expression, timezone)
            self._scheduler.add_job(
                _execute_schedule,
                trigger,
                args=[schedule_id],
                id=job_id,
                replace_existing=True,
            )
            logger.info("Added APScheduler job %s (cron: %s, tz: %s)", job_id, cron_expression, timezone)
        except Exception:
            logger.exception("Failed to add APScheduler job for schedule %s", schedule_id)

    def update_schedule(self, schedule_id: int, cron_expression: str, timezone: str = "UTC", scheduled_at=None, is_one_time: bool = False):
        """Reschedule an existing job with new parameters."""
        if not self._scheduler or not self._scheduler.running:
            return
        job_id = _schedule_job_id(schedule_id)
        try:
            if is_one_time and scheduled_at:
                trigger = _datetime_to_date_trigger(scheduled_at, timezone)
            else:
                trigger = _cron_to_trigger(cron_expression, timezone)
            existing = self._scheduler.get_job(job_id)
            if existing:
                self._scheduler.reschedule_job(job_id, trigger=trigger)
                logger.info("Rescheduled APScheduler job %s (cron: %s, tz: %s)", job_id, cron_expression, timezone)
            else:
                self.add_schedule(schedule_id, cron_expression, timezone, scheduled_at, is_one_time)
        except Exception:
            logger.exception("Failed to update APScheduler job for schedule %s", schedule_id)

    def remove_schedule(self, schedule_id: int):
        """Remove a CronTrigger job from the scheduler."""
        if not self._scheduler or not self._scheduler.running:
            return
        job_id = _schedule_job_id(schedule_id)
        try:
            existing = self._scheduler.get_job(job_id)
            if existing:
                self._scheduler.remove_job(job_id)
                logger.info("Removed APScheduler job %s", job_id)
        except Exception:
            logger.exception("Failed to remove APScheduler job for schedule %s", schedule_id)

    def load_all_schedules(self):
        """Load all active schedules from the DB and register CronTrigger jobs."""
        db = SessionLocal()
        try:
            active_schedules = (
                db.query(JobSchedule)
                .filter(JobSchedule.status == ScheduleStatus.ACTIVE)
                .all()
            )
            for schedule in active_schedules:
                self.add_schedule(
                    schedule.id,
                    schedule.cron_expression,
                    timezone=schedule.timezone or "UTC",
                    scheduled_at=schedule.scheduled_at,
                    is_one_time=schedule.is_one_time,
                )
            logger.info("Loaded %d active schedule(s) from DB", len(active_schedules))
        except Exception:
            logger.exception("Failed to load schedules from DB")
        finally:
            db.close()
