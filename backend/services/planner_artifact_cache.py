"""
Optional read-through Redis cache for GET /jobs/{id}/planner-artifacts/{id}/raw.

Off by default (empty PLANNER_ARTIFACT_CACHE_REDIS_URL). Use a dedicated Redis DB
when enabling — do not share with Celery broker without understanding key eviction.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

# None = not initialized; False = redis extra missing (do not retry import); otherwise Redis client.
_client = None


def _get_client():
    global _client
    url = (getattr(settings, "PLANNER_ARTIFACT_CACHE_REDIS_URL", None) or "").strip()
    if not url:
        return None
    if _client is False:
        return None
    if _client is not None:
        return _client
    try:
        import redis
    except ModuleNotFoundError:
        logger.warning(
            "planner_artifact_cache: redis package not installed; ignoring PLANNER_ARTIFACT_CACHE_REDIS_URL",
        )
        _client = False
        return None
    _client = redis.Redis.from_url(
        url,
        decode_responses=False,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    return _client


def planner_raw_cache_key(job_id: int, artifact_id: int) -> str:
    return f"sandhi:planner_raw:v1:{job_id}:{artifact_id}"


def get_cached_planner_raw(job_id: int, artifact_id: int) -> Optional[bytes]:
    r = _get_client()
    if r is None:
        return None
    try:
        key = planner_raw_cache_key(job_id, artifact_id)
        val = r.get(key)
        if val is None:
            return None
        if isinstance(val, memoryview):
            return val.tobytes()
        if isinstance(val, bytes):
            return val
        return None
    except Exception as exc:
        logger.warning(
            "planner_artifact_cache_get_fail job_id=%s artifact_id=%s error=%s",
            job_id,
            artifact_id,
            type(exc).__name__,
        )
        return None


def set_cached_planner_raw(job_id: int, artifact_id: int, data: bytes) -> None:
    r = _get_client()
    if r is None or not data:
        return
    raw_ttl = getattr(settings, "PLANNER_ARTIFACT_CACHE_TTL_SECONDS", 300)
    try:
        ttl = int(raw_ttl)
    except (TypeError, ValueError):
        ttl = 300
    if ttl <= 0:
        return
    try:
        key = planner_raw_cache_key(job_id, artifact_id)
        r.setex(key, ttl, data)
    except Exception as exc:
        logger.warning(
            "planner_artifact_cache_set_fail job_id=%s artifact_id=%s error=%s",
            job_id,
            artifact_id,
            type(exc).__name__,
        )
