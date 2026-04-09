"""
Execution heartbeat service.

Design goals:
- Keep Redis as hot, ephemeral runtime state for high-frequency observability.
- Persist throttled, durable DB snapshots for stuck diagnosis after Redis loss/restart.
- Never let heartbeat failures break job execution.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.config import settings

logger = logging.getLogger(__name__)

# None = not initialized; False = redis extra missing; otherwise redis client.
_redis_client = None


def _utc_now() -> datetime:
    return datetime.utcnow()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _coerce_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _heartbeat_redis_url() -> str:
    explicit = (getattr(settings, "HEARTBEAT_REDIS_URL", None) or "").strip()
    if explicit:
        return explicit
    return (getattr(settings, "CELERY_BROKER_URL", None) or "").strip()


def _get_redis_client():
    global _redis_client
    if not bool(getattr(settings, "HEARTBEAT_ENABLE_REDIS", True)):
        return None
    url = _heartbeat_redis_url()
    if not url:
        return None
    if _redis_client is False:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
    except ModuleNotFoundError:
        logger.warning("execution_heartbeat: redis package not installed; live Redis heartbeats disabled")
        _redis_client = False
        return None
    _redis_client = redis.Redis.from_url(
        url,
        decode_responses=False,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    return _redis_client


def heartbeat_redis_key(job_id: int, workflow_step_id: int) -> str:
    return f"sandhi:step_live:v1:{job_id}:{workflow_step_id}"


def get_step_live_state(job_id: int, workflow_step_id: int) -> Optional[Dict[str, Any]]:
    """
    Read one step live-state payload from Redis.
    Returns None on missing key, disabled Redis, decode errors, or Redis errors.
    """
    r = _get_redis_client()
    if r is None:
        return None
    try:
        raw = r.get(heartbeat_redis_key(job_id, workflow_step_id))
        if raw is None:
            return None
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        if isinstance(raw, bytes):
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        elif isinstance(raw, str):
            payload = json.loads(raw)
        else:
            return None
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _sanitize_reason_detail(reason_detail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(reason_detail, dict):
        return None
    safe: Dict[str, Any] = {}
    for k, v in reason_detail.items():
        key = str(k)[:64]
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe[key] = v
        else:
            safe[key] = str(v)[:256]
    return safe


def _safe_detail_bytes(detail: Optional[Dict[str, Any]]) -> Optional[str]:
    if not detail:
        return None
    try:
        return json.dumps(detail, ensure_ascii=False, separators=(",", ":"), default=str)[:2000]
    except Exception:
        return None


def _should_persist_db_snapshot(step, *, phase: str, now: datetime, meaningful_progress: bool) -> bool:
    if not bool(getattr(settings, "HEARTBEAT_ENABLE_DB_SNAPSHOT", True)):
        return False
    if meaningful_progress:
        return True
    old_phase = getattr(step, "live_phase", None)
    if old_phase != phase:
        return True
    min_seconds = max(5, _coerce_int(getattr(settings, "HEARTBEAT_DB_MIN_UPDATE_SECONDS", 45), 45))
    last_activity = getattr(step, "last_activity_at", None)
    if last_activity is None:
        return True
    return (now - last_activity).total_seconds() >= float(min_seconds)


def publish_step_heartbeat(
    *,
    db,
    step,
    phase: str,
    reason_code: str,
    message: Optional[str] = None,
    reason_detail: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    attempt: Optional[int] = None,
    max_retries: Optional[int] = None,
    execution_token: Optional[str] = None,
    meaningful_progress: bool = False,
    commit_db: bool = False,
) -> None:
    """
    Publish a workflow-step heartbeat to Redis and persist a throttled durable DB snapshot.
    Must never raise.
    """
    try:
        now = _utc_now()
        reason = (reason_code or "unknown").strip()[:64]
        phase_val = (phase or "working").strip()[:32]
        msg = (message or "").strip()[:240] if message else None
        detail = _sanitize_reason_detail(reason_detail)
        detail_json = _safe_detail_bytes(detail)

        # Durable fallback snapshot for diagnosis when Redis is unavailable/restarted.
        if _should_persist_db_snapshot(step, phase=phase_val, now=now, meaningful_progress=meaningful_progress):
            old_phase = getattr(step, "live_phase", None)
            step.live_phase = phase_val
            if old_phase != phase_val:
                step.live_phase_started_at = now
            step.live_reason_code = reason
            step.live_reason_detail = detail_json or msg
            step.live_trace_id = (trace_id or "")[:64] or None
            step.live_attempt = attempt
            step.last_activity_at = now
            if meaningful_progress:
                step.last_progress_at = now
                step.stuck_since = None
                step.stuck_reason = None
            if commit_db:
                db.commit()

        r = _get_redis_client()
        if r is not None:
            ttl = max(30, _coerce_int(getattr(settings, "HEARTBEAT_REDIS_TTL_SECONDS", 180), 180))
            payload = {
                "schema_version": "sandhi.step_live.v1",
                "job_id": int(getattr(step, "job_id")),
                "workflow_step_id": int(getattr(step, "id")),
                "step_order": int(getattr(step, "step_order") or 0),
                "agent_id": int(getattr(step, "agent_id") or 0),
                "execution_token": execution_token or None,
                "phase": phase_val,
                "phase_started_at": (
                    getattr(step, "live_phase_started_at", None).isoformat()
                    if getattr(step, "live_phase_started_at", None)
                    else _iso_now()
                ),
                "message": msg,
                "reason_code": reason,
                "reason_detail": detail,
                "reason_detail_json": detail_json,
                "attempt": attempt,
                "max_retries": max_retries,
                "last_update_ts": _iso_now(),
                "last_progress_ts": (
                    getattr(step, "last_progress_at", None).isoformat()
                    if getattr(step, "last_progress_at", None)
                    else None
                ),
                "trace_id": (trace_id or "")[:64] or None,
            }
            key = heartbeat_redis_key(int(getattr(step, "job_id")), int(getattr(step, "id")))
            r.setex(key, ttl, _safe_json_bytes(payload))
    except Exception as exc:
        logger.warning(
            "execution_heartbeat_publish_failed step_id=%s phase=%s error=%s",
            getattr(step, "id", None),
            phase,
            type(exc).__name__,
        )
