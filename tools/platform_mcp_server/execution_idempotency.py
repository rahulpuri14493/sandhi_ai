"""Idempotency for platform MCP sends: Redis when configured, else in-process (single worker)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_MAX_MEM_ENTRIES = 512
_TTL_DEFAULT = 3600.0
_STORE: dict[str, tuple[float, str]] = {}

_REDIS_PREFIX = "sandhi:platform_mcp:idemp:v1:"
_redis_client: Optional[object] = None
_redis_cooldown_until: float = 0.0


def _idempotency_ttl_seconds() -> int:
    raw = (os.environ.get("PLATFORM_MCP_IDEMPOTENCY_TTL_SECONDS") or "").strip()
    try:
        v = int(raw) if raw else int(_TTL_DEFAULT)
    except ValueError:
        v = int(_TTL_DEFAULT)
    return max(60, min(v, 86400))


def _redis_url() -> str:
    for key in (
        "PLATFORM_MCP_IDEMPOTENCY_REDIS_URL",
        "REDIS_URL",
        "MCP_GUARDRAILS_REDIS_URL",
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return ""


def _get_redis():
    """Return a shared Redis client or None (cooldown after connect failures)."""
    global _redis_client, _redis_cooldown_until
    now = time.monotonic()
    if now < _redis_cooldown_until:
        return None
    if _redis_client is not None:
        return _redis_client
    url = _redis_url()
    if not url:
        return None
    try:
        import redis as redis_mod

        r = redis_mod.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        r.ping()
        _redis_client = r
        logger.info("Platform MCP idempotency: using Redis")
        return _redis_client
    except Exception as exc:
        logger.warning(
            "Platform MCP idempotency: Redis unavailable (%s); using in-process cache only.",
            type(exc).__name__,
        )
        _redis_cooldown_until = now + 60.0
        return None


def _redis_get(key: str) -> Optional[str]:
    r = _get_redis()
    if r is None:
        return None
    try:
        v = r.get(key)
        return v if v else None
    except Exception:
        logger.debug("idempotency redis get failed", exc_info=True)
        return None


def _redis_set(key: str, value: str, ttl: int) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(key, ttl, value)
    except Exception:
        logger.debug("idempotency redis setex failed", exc_info=True)


def _is_ok_tool_json(payload: str) -> bool:
    try:
        data = json.loads(payload)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("status") == "ok"


def cached_tool_json(
    scope: str,
    idempotency_key: str,
    factory: Callable[[], str],
    *,
    cache_success_only: bool = False,
) -> str:
    """
    If idempotency_key is non-empty, return a prior response for the same scope+key within TTL.

    When ``PLATFORM_MCP_IDEMPOTENCY_REDIS_URL`` (or ``REDIS_URL`` / ``MCP_GUARDRAILS_REDIS_URL``)
    is set, successful payloads are shared across all platform MCP workers.

    When cache_success_only is True, only successful JSON bodies (top-level status \"ok\") are stored.
    """
    key = (idempotency_key or "").strip()
    if not key:
        return factory()
    h = hashlib.sha256(f"{scope}\0{key}".encode("utf-8")).hexdigest()
    redis_key = _REDIS_PREFIX + h
    ttl_sec = _idempotency_ttl_seconds()
    now = time.monotonic()

    hit_redis = _redis_get(redis_key)
    if hit_redis is not None:
        return hit_redis

    if len(_STORE) > _MAX_MEM_ENTRIES:
        cutoff = now - float(ttl_sec)
        stale = [k for k, (t, _) in _STORE.items() if t < cutoff]
        for k in stale[: _MAX_MEM_ENTRIES // 2]:
            _STORE.pop(k, None)
    mem_hit = _STORE.get(h)
    if mem_hit and now - mem_hit[0] < float(ttl_sec):
        return mem_hit[1]

    out = factory()
    if cache_success_only and not _is_ok_tool_json(out):
        return out
    _redis_set(redis_key, out, ttl_sec)
    _STORE[h] = (now, out)
    return out
