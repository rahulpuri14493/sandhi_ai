"""
Business/end-user job lifecycle webhook alerts.

Emits low-noise events for job_started, job_stuck, job_failed, job_completed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is False:
        return None
    if _redis_client is not None:
        return _redis_client
    url = (getattr(settings, "HEARTBEAT_REDIS_URL", None) or "").strip()
    if not url:
        url = (getattr(settings, "CELERY_BROKER_URL", None) or "").strip()
    if not url:
        return None
    try:
        import redis
    except ModuleNotFoundError:
        _redis_client = False
        return None
    try:
        _redis_client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    except Exception:
        _redis_client = False
    return _redis_client if _redis_client is not False else None


def _dedupe_key(job_id: int, event_type: str) -> str:
    return f"sandhi:business_job_alert:v1:{job_id}:{event_type}"


def send_business_job_alert(
    *,
    event_type: str,
    job_id: int,
    business_id: int,
    title: str,
    status: str,
    stage: Optional[str] = None,
    reason: Optional[str] = None,
    share_url: Optional[str] = None,
) -> None:
    if not bool(getattr(settings, "BUSINESS_JOB_ALERTS_ENABLED", False)):
        return
    webhook = (getattr(settings, "BUSINESS_JOB_ALERT_WEBHOOK_URL", None) or "").strip()
    if not webhook:
        return

    cooldown = max(30, int(getattr(settings, "BUSINESS_JOB_ALERT_COOLDOWN_SECONDS", 180) or 180))
    fp = json.dumps(
        {
            "event_type": event_type,
            "status": status,
            "stage": stage or "",
            "reason": reason or "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    r = _get_redis_client()
    if r is not None:
        try:
            key = _dedupe_key(int(job_id), str(event_type))
            prev = r.get(key)
            if prev == fp:
                return
            r.setex(key, cooldown, fp)
        except Exception:
            pass

    payload = {
        "event": "business_job_lifecycle",
        "event_type": event_type,
        "job_id": int(job_id),
        "business_id": int(business_id),
        "title": title,
        "status": status,
        "stage": stage,
        "reason": reason,
        "share_url": share_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with httpx.Client(timeout=6.0) as client:
            client.post(webhook, json=payload)
    except Exception as exc:
        logger.warning("business_job_alert_send_fail job_id=%s event=%s err=%s", job_id, event_type, type(exc).__name__)

