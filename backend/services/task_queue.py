"""Celery task queue integration with safe local fallback."""

from __future__ import annotations

import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)


class QueueEnqueueError(RuntimeError):
    """Raised when strict queue mode is enabled and enqueue fails."""



try:
    from celery import Celery
except Exception:  # pragma: no cover - optional dependency at runtime
    Celery = None


celery_app: Optional["Celery"] = None
if Celery is not None:
    celery_app = Celery(
        "sandhi_ai",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
    )

    # Configure Celery Beat
    celery_app.conf.beat_schedule = {
        "check-stuck-jobs-every-30-mins": {
            "task": "check_stuck_jobs",
            "schedule": 1800.0,  # 30 minutes in seconds
        },
    }


def enqueue_execute_platform_job(
    job_id: int,
    history_id: Optional[int] = None,
    execution_token: Optional[str] = None,
    *,
    strict: bool = False,
) -> bool:
    """
    Enqueue execution in Celery when configured.
    Returns True when enqueued, False when caller should fallback.
    """
    if (settings.JOB_EXECUTION_BACKEND or "").strip().lower() != "celery":
        return False
    if celery_app is None:
        msg = f"Celery not installed for job_id={job_id}"
        if strict:
            raise QueueEnqueueError(msg)
        logger.warning("%s; falling back to local thread", msg)
        return False
    try:
        execute_platform_job.delay(job_id=job_id, history_id=history_id, execution_token=execution_token)
        return True
    except Exception as e:
        if strict:
            raise QueueEnqueueError(f"Failed to enqueue job_id={job_id} to Celery: {e}") from e
        logger.warning("Failed to enqueue job_id=%s to Celery: %s; falling back to local thread", job_id, e)
        return False


def get_queue_health() -> dict:
    """
    Lightweight queue health probe for /health.
    Checks backend mode + Redis reachability when celery mode is enabled.
    """
    mode = (settings.JOB_EXECUTION_BACKEND or "celery").strip().lower()
    if mode != "celery":
        return {"ok": True, "detail": f"execution_backend={mode}"}
    strict_queue = bool(getattr(settings, "JOB_EXECUTION_STRICT_QUEUE", False))
    try:
        import redis  # lazy import for environments without redis extra

        client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2, socket_connect_timeout=2)
        pong = client.ping()
        if pong:
            return {"ok": True, "detail": "celery broker reachable"}
        if strict_queue:
            return {"ok": False, "detail": "celery broker ping failed (strict queue enabled)"}
        return {"ok": True, "detail": "celery broker ping failed; local fallback enabled"}
    except Exception:
        if strict_queue:
            return {"ok": False, "detail": "celery broker unreachable (strict queue enabled)"}
        return {"ok": True, "detail": "celery broker unreachable; local fallback enabled"}


def get_queue_stats() -> dict:
    """
    Queue runtime stats for ops dashboards/troubleshooting.
    Returns pending queue depth and worker active/reserved counts when available.
    """
    mode = (settings.JOB_EXECUTION_BACKEND or "celery").strip().lower()
    out = {
        "execution_backend": mode,
        "queue_name": "celery",
        "pending_jobs": None,
        "workers": {"online": 0, "active": 0, "reserved": 0},
    }
    if mode != "celery":
        return out

    # Redis queue depth (default Celery queue key = "celery")
    try:
        import redis

        client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2, socket_connect_timeout=2)
        out["pending_jobs"] = int(client.llen("celery"))
    except Exception:
        out["pending_jobs"] = None

    # Celery worker runtime stats (best effort)
    if celery_app is not None:
        try:
            insp = celery_app.control.inspect(timeout=1.0)
            active = insp.active() or {}
            reserved = insp.reserved() or {}
            stats = insp.stats() or {}
            out["workers"]["online"] = len(stats)
            out["workers"]["active"] = sum(len(v or []) for v in active.values())
            out["workers"]["reserved"] = sum(len(v or []) for v in reserved.values())
        except Exception:
            pass
    return out


if celery_app is not None:
    @celery_app.task(name="execute_platform_job", bind=True)
    def execute_platform_job(self, job_id: int, history_id: Optional[int] = None, execution_token: Optional[str] = None):
        """
        Celery worker task. Uses fresh DB session + event loop through shared runner.
        """
        from services.job_scheduler import run_job_in_thread
        try:
            return run_job_in_thread(job_id=job_id, history_id=history_id, execution_token=execution_token)
        except Exception as e:
            max_retries = max(0, int(getattr(settings, "CELERY_EXECUTE_MAX_RETRIES", 3)))
            if self.request.retries < max_retries:
                countdown = max(1, int(getattr(settings, "CELERY_EXECUTE_RETRY_BACKOFF_SECONDS", 5)))
                raise self.retry(exc=e, countdown=countdown)
            raise

    @celery_app.task(name="trigger_scheduled_job", bind=True)
    def trigger_scheduled_job(self, schedule_id: int):
        """
        Celery worker task for ETA schedules. This replaces the previous
        in-process APScheduler DateTrigger implementation.
        """
        from services.job_scheduler import _execute_schedule
        try:
            # Reuses the core execution logic now triggered via distributed worker instead of in-process scheduler callback
            _execute_schedule(schedule_id)
        except Exception as e:
            logger.exception("Scheduled ETA task failed for schedule_id=%s", schedule_id)
            raise


    # ---------------------------------------------------------------------------
    # Stuck job watchdog
    # ---------------------------------------------------------------------------

    @celery_app.task(name="check_stuck_jobs")
    def _check_stuck_jobs():
        """Periodic watchdog: detect jobs stuck in IN_PROGRESS or IN_QUEUE.

        Uses schedule.last_run_time (when the schedule last fired) as the reference
        point — NOT Job.created_at (which is when the job was first created and could
        be much older than the current execution).

        Does NOT kill the job — just logs a warning and creates a history entry.
        The frontend can show a warning banner so the user decides to cancel or wait.
        """
        from core.config import settings
        from datetime import datetime, timedelta
        from db.database import SessionLocal
        from models.job import Job, JobStatus
        from models.job import (
            Job, JobSchedule, JobStatus, ScheduleStatus,
            WorkflowStep, ScheduleExecutionHistory,
        )

        threshold_hours = getattr(settings, "STUCK_JOB_THRESHOLD_HOURS", 6)
        cutoff = datetime.utcnow() - timedelta(hours=threshold_hours)

        db = SessionLocal()
        try:
            # Find jobs whose schedule fired before the cutoff and are still running/queued
            stuck_rows = (
                db.query(Job, JobSchedule)
                .join(JobSchedule, JobSchedule.job_id == Job.id)
                .filter(
                    Job.status.in_([JobStatus.IN_PROGRESS, JobStatus.IN_QUEUE]),
                    JobSchedule.last_run_time.isnot(None),
                    JobSchedule.last_run_time < cutoff,
                )
                .all()
            )
            for job, schedule in stuck_rows:
                logger.warning(
                    "Job %s has been %s since schedule fired at %s (over %d hours) — potentially stuck",
                    job.id, job.status.value, schedule.last_run_time, threshold_hours,
                )
                # Avoid duplicate entries — only create one potentially_stuck record per job
                existing = (
                    db.query(ScheduleExecutionHistory)
                    .filter(
                        ScheduleExecutionHistory.job_id == job.id,
                        ScheduleExecutionHistory.status == "potentially_stuck",
                    )
                    .first()
                )
                if not existing:
                    history = ScheduleExecutionHistory(
                        schedule_id=schedule.id,
                        job_id=job.id,
                        status="potentially_stuck",
                        failure_reason=f"Job has been {job.status.value} for over {threshold_hours} hours",
                        triggered_by="watchdog",
                    )
                    db.add(history)
            db.commit()
        except Exception:
            logger.exception("Error in stuck job watchdog")
            db.rollback()
        finally:
            db.close()

