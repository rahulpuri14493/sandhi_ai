"""Celery task queue integration with safe local fallback."""

from __future__ import annotations

import logging
import redis
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from kombu import Queue

from core.config import settings
from services.business_job_alerts import send_business_job_alert

logger = logging.getLogger(__name__)


def cleanup_heartbeat_retention_once() -> dict:
    """
    Cleanup durable heartbeat telemetry older than retention window.

    Redis heartbeat and nonce keys already use short TTLs; this task focuses on
    durable DB snapshot fields on workflow steps to control storage growth.
    """
    from db.database import SessionLocal
    from models.job import WorkflowStep

    retention_days = max(1, int(getattr(settings, "HEARTBEAT_RETENTION_DAYS", 30) or 30))
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    db = SessionLocal()
    cleared = 0
    try:
        # Only clean non-active steps and only when the newest known runtime signal
        # is older than the retention cutoff.
        rows = (
            db.query(WorkflowStep)
            .filter(WorkflowStep.status != "in_progress")
            .all()
        )
        for step in rows:
            has_live = any(
                [
                    getattr(step, "live_phase", None),
                    getattr(step, "live_phase_started_at", None),
                    getattr(step, "live_reason_code", None),
                    getattr(step, "live_reason_detail", None),
                    getattr(step, "live_trace_id", None),
                    getattr(step, "live_attempt", None) is not None,
                    getattr(step, "stuck_since", None),
                    getattr(step, "stuck_reason", None),
                ]
            )
            if not has_live:
                continue
            latest_ts = max(
                [
                    ts
                    for ts in [
                        getattr(step, "completed_at", None),
                        getattr(step, "last_activity_at", None),
                        getattr(step, "last_progress_at", None),
                        getattr(step, "live_phase_started_at", None),
                        getattr(step, "started_at", None),
                    ]
                    if ts is not None
                ],
                default=None,
            )
            if latest_ts is None or latest_ts >= cutoff:
                continue
            step.live_phase = None
            step.live_phase_started_at = None
            step.live_reason_code = None
            step.live_reason_detail = None
            step.live_trace_id = None
            step.live_attempt = None
            step.stuck_since = None
            step.stuck_reason = None
            cleared += 1
        if cleared:
            db.commit()
        return {"retention_days": retention_days, "cutoff": cutoff.isoformat(), "cleared_steps": int(cleared)}
    except Exception:
        db.rollback()
        logger.exception("heartbeat_retention_cleanup_failed")
        return {"retention_days": retention_days, "cutoff": cutoff.isoformat(), "cleared_steps": int(cleared), "error": True}
    finally:
        db.close()


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

        # --- Reliability settings ---
        task_acks_late=True,
        task_reject_on_worker_lost=True, # Requeues job if worker processing it is lost (e.g crash, OOM)

        # --- Priority Isolation ---
        task_default_queue=getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "interactive"),
        task_queues=(
            Queue("interactive", routing_key="interactive"),
            Queue("batch", routing_key="batch"),
        ),
        task_routes={
            # Direct API executions go to the fast lane
            "execute_platform_job": {"queue": "interactive"},
            # Scheduled/ETA tasks go to the background lane
            "trigger_scheduled_job": {"queue": "batch"},
            "check_stuck_jobs": {"queue": "batch"},
            "check_stuck_workflow_steps": {"queue": "batch"},
        }
    )

    # Configure Celery Beat
    celery_app.conf.beat_schedule = {
        "check-stuck-jobs-every-30-mins": {
            "task": "check_stuck_jobs",
            "schedule": 1800.0,  # 30 minutes in seconds
        },
        "check-stuck-workflow-steps-every-2-mins": {
            "task": "check_stuck_workflow_steps",
            "schedule": 120.0,
        },
        "heartbeat-retention-cleanup-daily": {
            "task": "cleanup_heartbeat_retention",
            "schedule": 86400.0,
        },
    }

# ---------------------------------------------------------------------------
# Helpers for enqueueing with admission control and backpressure
# ---------------------------------------------------------------------------

def _get_queue_oldest_age_seconds(client: redis.Redis, redis_key: str) -> Optional[float]:
    """
    Peek at the oldest task in the queue and return its age in seconds.
    Celery stores tasks as JSON. The 'eta' field or 'headers.enqueued_at' 
    gives us the enqueue timestamp.
    Returns None if the queue is empty or the timestamp can't be parsed.
    """
    try:
        # Celery/Kombu uses Redis lists as FIFO; index -1 (tail) represents the oldest enqueued task.
        raw = client.lindex(redis_key, -1)  # oldest item is at the tail
        if not raw:
            return None
        task = json.loads(raw)

        # Try headers.enqueued_at first (Celery 4+)
        timestamp_str = (task.get("headers") or {}).get("enqueued_at")

        # Fall back to eta if present
        if not timestamp_str:
            timestamp_str = task.get("eta")

        if not timestamp_str:
            return None

        # Parse ISO 8601 timestamp
        enqueued_at = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - enqueued_at).total_seconds()

    except Exception as e:
        logger.warning("Failed to parse oldest task age for key '%s': %s", redis_key, e)
        return None
    
# ---------------------------------------------------------------------------
# Main enqueue function with admission control and backpressure
# ---------------------------------------------------------------------------


# Fallback symbol for imports when Celery is unavailable in the environment.
def trigger_scheduled_job(*args, **kwargs):  # pragma: no cover - runtime fallback
    raise RuntimeError("Celery is not installed; trigger_scheduled_job task is unavailable")


class ExecutePlatformJobTask:
    """
    Task-like fallback used by tests and non-celery environments.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # pragma: no cover - exercised via tests
        job_id = kwargs.get("job_id") or (args[0] if args else None)
        history_id = kwargs.get("history_id")
        logger.error("Job %s permanently failed after retries exhausted. Error: %s", job_id, exc)

        from db.database import SessionLocal
        from models.job import Job, JobStatus, ScheduleExecutionHistory
        from datetime import datetime as _dt

        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job and job.status == JobStatus.IN_PROGRESS:
                job.status = JobStatus.FAILED
                job.failure_reason = f"Execution permanently failed: {str(exc)[:450]}"
                if history_id:
                    hist = db.query(ScheduleExecutionHistory).filter(
                        ScheduleExecutionHistory.id == history_id
                    ).first()
                    if hist:
                        hist.status = "failed"
                        hist.failure_reason = f"Permanent failure after retries: {str(exc)[:450]}"
                        hist.completed_at = _dt.utcnow()
                db.commit()
        except Exception:
            logger.exception("Failed to mark job as FAILED in on_failure hook")
        finally:
            db.close()


def _execute_platform_job_core(job_id: int, history_id: Optional[int] = None, execution_token: Optional[str] = None):
    """
    Shared execution entrypoint. Raises so Celery retry policies can apply.
    """
    from services.job_scheduler import run_job_in_thread

    return run_job_in_thread(
        job_id=job_id,
        history_id=history_id,
        execution_token=execution_token,
        reraise_exceptions=True,
    )


def execute_platform_job(job_id: int, history_id: Optional[int] = None, execution_token: Optional[str] = None):
    """Public callable used by tests and non-celery paths."""
    return _execute_platform_job_core(
        job_id=job_id,
        history_id=history_id,
        execution_token=execution_token,
    )


def enqueue_execute_platform_job(
    job_id: int,
    history_id: Optional[int] = None,
    execution_token: Optional[str] = None,
    *,
    strict: bool = False,
    queue_name: str = "interactive",  # Default queue to the fast lane for direct API executions
) -> bool:
    """
    Enqueue execution with admission control and backpressure.
    """
    if (settings.JOB_EXECUTION_BACKEND or "").strip().lower() != "celery":
        return False
    if celery_app is None:
        msg = f"Celery not installed for job_id={job_id}"
        if strict:
            raise QueueEnqueueError(msg)
        logger.warning("%s; falling back to local thread", msg)
        return False

    # --- Admission Control: Check if the target queue is healthy before enqueuing ---
    cb_key = f"circuit_breaker:{queue_name}"
    redis_key = f"{settings.CELERY_QUEUE_PREFIX}{queue_name}"
    breach_threshold = getattr(settings, "CELERY_CIRCUIT_BREACH_THRESHOLD", 30)
    max_depth = getattr(settings, "CELERY_MAX_QUEUE_DEPTH", 100)

    # --- Lua script for atomic read-and-evaluate
    ADMISSION_SCRIPT = """
    local cb_key = KEYS[1]
    local queue_key = KEYS[2]
    local breach_threshold = tonumber(ARGV[1])
    local max_depth = tonumber(ARGV[2])

    -- Check Circuit Breaker
    local cb_value = tonumber(redis.call('GET', cb_key) or '0')
    if cb_value >= breach_threshold then
        return -1 -- Circuit breaker OPEN
    end

    -- Check Queue Depth
    local current_depth = tonumber(redis.call('LLEN', queue_key) or '0')
    if current_depth >= max_depth then
        redis.call('INCR', cb_key)
        redis.call('EXPIRE', cb_key, 60)
        return -2 -- Backpressure TRIPPED
    end

    return 1 -- OK to proceed
    
    """

    try:
        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL, socket_timeout=2, socket_connect_timeout=2
        )
    except redis.RedisError as e:
        logger.warning("Failed to connect to Redis for admission check: %s", e)
        client = None

    if client is not None:
        try:
            # Execute atomic check: 2 KEYS, 2 ARGS
            result = client.eval(
                ADMISSION_SCRIPT, 2, cb_key, redis_key, breach_threshold, max_depth
            )

            if result == -1:
                logger.warning(
                    "Circuit breaker OPEN for queue '%s' (threshold=%d)",
                    queue_name, breach_threshold,
                )
                if strict:
                    raise QueueEnqueueError(f"Circuit breaker OPEN for queue '{queue_name}'. Traffic rejected.")
                return False
            
            if result == -2:
                logger.warning(
                    "BACKPRESSURE TRIPPED: Queue '%s' exceeds max (%d)",
                    queue_name, max_depth,
                )
                if strict:
                    raise QueueEnqueueError(f"Queue '{queue_name}' is overloaded.")
                return False
        
        except redis.RedisError as e:
            logger.warning("Failed to execute admission control script: %s", e)
            # Fail open: skip admission control and attempt enqueue

    # --- Enqueue the task ---
    try:
        execute_platform_job.apply_async(
            kwargs={"job_id": job_id, "history_id": history_id, "execution_token": execution_token},
            queue=queue_name,
        )
        return True
    except Exception as e:
        if strict:
            raise QueueEnqueueError(f"Failed to enqueue job_id={job_id} to Celery: {e}") from e
        logger.warning(
            "Failed to enqueue job_id=%s to Celery: %s; falling back to local thread",
            job_id, e,
        )
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
    Returns pending queue depth, circuit breaker state, and worker active/reserved counts.
    """
    mode = (settings.JOB_EXECUTION_BACKEND or "celery").strip().lower()
    slo_max_age = getattr(settings, "CELERY_SLO_MAX_QUEUE_AGE_SECONDS", 1800)
    slo_p95_threshold = getattr(settings, "CELERY_SLO_ENQUEUE_TO_START_P95_SECONDS", 300)

    out = {
        "execution_backend": mode,
        "queue_name": "celery",
        "queues": {},
        "slo_targets": {
            "p95_start_delay_seconds": slo_p95_threshold
        },
        "pending_jobs": None,
        "workers": {"online": 0, "active": 0, "reserved": 0},
    }
    if mode != "celery":
        return out

    target_queues = ["interactive", "batch"]
    breach_threshold = getattr(settings, "CELERY_CIRCUIT_BREACH_THRESHOLD", 30)

    try:
        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL, socket_timeout=2, socket_connect_timeout=2
        )
        for q in target_queues:
            redis_key = f"{settings.CELERY_QUEUE_PREFIX}{q}"
            cb_key = f"circuit_breaker:{q}"

            pipe = client.pipeline()
            pipe.llen(redis_key)
            pipe.get(cb_key)
            results = pipe.execute()

            depth = int(results[0] or 0)
            cb_value = int(results[1] or 0)
            oldest_age = _get_queue_oldest_age_seconds(client, redis_key)

            out["queues"][q] = {
                "pending_jobs": depth,
                "oldest_job_age_seconds": oldest_age,
                "slo_max_age_seconds": slo_max_age,
                "slo_age_breached": oldest_age is not None and oldest_age > slo_max_age,
                "circuit_breaker_count": cb_value,
                "circuit_breaker_open": cb_value >= breach_threshold,
            }
    except Exception:
        for q in target_queues:
            out["queues"][q] = {
                "pending_jobs": None,
                "oldest_job_age_seconds": None,
                "slo_max_age_seconds": slo_max_age, # Return the config default
                "slo_age_breached": None,
                "circuit_breaker_count": None,
                "circuit_breaker_open": None,
            }

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

    class ExecutePlatformJobTask(celery_app.Task):
        """Custom Task class to handle final failure after retries exhausted, if needed."""
        def on_failure(self, exc, task_id, args, kwargs, einfo):
            # This triggers ONLY when max_retries is exceeded or a non retryable exception is raised.
            job_id = kwargs.get("job_id") or (args[0] if args else None)
            history_id = kwargs.get("history_id")
            logger.error("Job %s permanently failed after retries exhausted. Error: %s", job_id, exc)

            from db.database import SessionLocal
            from models.job import Job, JobStatus, ScheduleExecutionHistory
            from datetime import datetime

            db = SessionLocal()
            try:
                # 1. Update Job Status
                job = db.query(Job).filter(Job.id == job_id).first()
                if job and job.status == JobStatus.IN_PROGRESS:
                    job.status = JobStatus.FAILED
                    job.failure_reason = f"Execution permanently failed: {str(exc)[:450]}"

                    # 2. Update Execution History
                    if history_id:
                        hist = db.query(ScheduleExecutionHistory).filter(
                            ScheduleExecutionHistory.id == history_id
                        ).first()
                        if hist:
                            hist.status = "failed"
                            hist.failure_reason = f"Permanent failure after retries: {str(exc)[:450]}"
                            hist.completed_at = datetime.utcnow()
                            logger.info("Updated execution history %s to FAILED", history_id)


                    db.commit()
                    logger.info("Marked job %s as FAILED in database after permanent failure", job_id)

            except Exception as e:
                logger.error("Failed to update job status to FAILED for job %s: %s", job_id, e)
            finally:
                db.close()

            super().on_failure(exc, task_id, args, kwargs, einfo)
    @celery_app.task(
        name="execute_platform_job",
        bind=True,
        base=ExecutePlatformJobTask,

        # --- Retry Policy ---
        autoretry_for=(Exception,),  # Retry for all exceptions
        max_retries= max(0, int(getattr(settings, "CELERY_EXECUTE_MAX_RETRIES", 3))),
        retry_backoff=getattr(settings, "CELERY_EXECUTE_RETRY_BACKOFF_SECONDS", 5),  # Exponential backoff base in seconds
        retry_backoff_max=getattr(settings, "CELERY_EXECUTE_RETRY_BACKOFF_MAX_SECONDS", 600),  # Max backoff time in seconds
        retry_jitter=True,  # Add random jitter to avoid thundering herd on retries

        # --- Worker Crash Handling ---
        acks_late=True,  # Acknowledge task only after execution
        reject_on_worker_lost=True,  # Requeue task if worker processing it is lost (e.g., crash, OOM)
    )
    def execute_platform_job(self, job_id: int, history_id: Optional[int] = None, execution_token: Optional[str] = None):
        """
        Celery worker task. Uses fresh DB session + event loop through shared runner.
        """
        return _execute_platform_job_core(
            job_id=job_id,
            history_id=history_id,
            execution_token=execution_token,
        )

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
        except Exception:
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
                    try:
                        send_business_job_alert(
                            event_type="job_stuck",
                            job_id=int(job.id),
                            business_id=int(job.business_id),
                            title=str(job.title or f"Job {job.id}"),
                            status=str(job.status.value),
                            stage="watchdog",
                            reason=f"Potentially stuck for over {threshold_hours}h",
                        )
                    except Exception:
                        pass
            db.commit()
        except Exception:
            logger.exception("Error in stuck job watchdog")
            db.rollback()
        finally:
            db.close()

    @celery_app.task(name="check_stuck_workflow_steps")
    def _check_stuck_workflow_steps():
        """
        Step-level watchdog using durable workflow-step telemetry snapshots.

        This path does not depend on Redis liveness, so stuck diagnosis survives Redis restarts.
        """
        from datetime import datetime, timedelta

        from db.database import SessionLocal
        from models.job import Job, JobStatus, WorkflowStep

        default_threshold = max(60, int(getattr(settings, "STEP_STUCK_THRESHOLD_SECONDS", 600)))
        blocked_threshold = max(
            default_threshold,
            int(getattr(settings, "STEP_STUCK_BLOCKED_THRESHOLD_SECONDS", 900)),
        )
        loop_round_threshold = max(3, int(getattr(settings, "STEP_LOOP_ROUND_THRESHOLD", 10)))
        repeat_tool_threshold = max(3, int(getattr(settings, "STEP_REPEAT_TOOLCALL_THRESHOLD", 6)))
        now = datetime.utcnow()

        db = SessionLocal()
        try:
            rows = (
                db.query(WorkflowStep, Job)
                .join(Job, Job.id == WorkflowStep.job_id)
                .filter(
                    WorkflowStep.status == "in_progress",
                    Job.status == JobStatus.IN_PROGRESS,
                )
                .all()
            )
            changed = 0
            for step, _job in rows:
                phase = (getattr(step, "live_phase", None) or "").strip().lower()
                effective_threshold = blocked_threshold if phase == "blocked" else default_threshold
                since = (
                    getattr(step, "last_progress_at", None)
                    or getattr(step, "last_activity_at", None)
                    or getattr(step, "started_at", None)
                )
                if since is None:
                    continue
                age_s = (now - since).total_seconds()
                if age_s >= float(effective_threshold):
                    if getattr(step, "stuck_since", None) is None:
                        step.stuck_since = now
                        # Best-effort classify looping vs slow external dependency.
                        detail_raw = getattr(step, "live_reason_detail", None) or ""
                        loop_round = None
                        same_tool_count = None
                        tool_name = None
                        try:
                            import json as _json
                            parsed = _json.loads(detail_raw) if detail_raw and detail_raw.strip().startswith("{") else None
                            if isinstance(parsed, dict):
                                loop = parsed.get("loop") if isinstance(parsed.get("loop"), dict) else None
                                if loop:
                                    loop_round = loop.get("round_idx")
                                same_tool_count = parsed.get("same_tool_count")
                                tool_name = parsed.get("tool_name")
                        except Exception:
                            parsed = None
                        kind = "stuck"
                        if (
                            (loop_round is not None and int(loop_round) >= loop_round_threshold)
                            or (same_tool_count is not None and int(same_tool_count) >= repeat_tool_threshold)
                        ):
                            kind = "looping"
                        if phase == "calling_tool" and kind != "looping":
                            kind = "slow_dependency"
                        base = f"{kind}: no meaningful progress for {int(age_s)}s"
                        extra = f" phase={phase or 'unknown'}"
                        if tool_name:
                            extra += f" tool={tool_name}"
                        step.stuck_reason = (base + ";" + extra)[:128]
                        changed += 1
                        logger.warning(
                            "workflow_step_potentially_stuck job_id=%s step_id=%s step_order=%s "
                            "phase=%s age_seconds=%s threshold_seconds=%s trace_id=%s",
                            step.job_id,
                            step.id,
                            step.step_order,
                            phase or "unknown",
                            int(age_s),
                            effective_threshold,
                            getattr(step, "live_trace_id", None),
                        )
                else:
                    # Auto-clear stale stuck flags once progress resumes.
                    if getattr(step, "stuck_since", None) is not None:
                        step.stuck_since = None
                        step.stuck_reason = None
                        changed += 1
            if changed:
                db.commit()
        except Exception:
            logger.exception("Error in step-level stuck watchdog")
            db.rollback()
        finally:
            db.close()

    @celery_app.task(name="cleanup_heartbeat_retention")
    def _cleanup_heartbeat_retention():
        result = cleanup_heartbeat_retention_once()
        logger.info(
            "heartbeat_retention_cleanup_done retention_days=%s cleared_steps=%s cutoff=%s",
            result.get("retention_days"),
            result.get("cleared_steps"),
            result.get("cutoff"),
        )

