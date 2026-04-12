"""Redis cache for successful MCP HTTP tool/call results (write_like + idempotency_key).

Uses Redis whenever ``MCP_GUARDRAILS_REDIS_URL`` (or optional ``MCP_HTTP_IDEMPOTENCY_REDIS_URL``)
is set — independent of ``MCP_GUARDRAILS_DISTRIBUTED_ENABLED`` so idempotency works with a
single backend worker + Redis.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional

from core.config import settings

logger = logging.getLogger(__name__)

_http_redis: Any = None
_http_redis_cooldown_until: float = 0.0


def _redis_url() -> str:
    dedicated = (getattr(settings, "MCP_HTTP_IDEMPOTENCY_REDIS_URL", "") or "").strip()
    if dedicated:
        return dedicated
    return (getattr(settings, "MCP_GUARDRAILS_REDIS_URL", "") or "").strip()


def get_http_idempotency_redis():
    """Shared Redis client for HTTP MCP idempotency; None if disabled or unreachable."""
    global _http_redis, _http_redis_cooldown_until
    if not bool(getattr(settings, "MCP_HTTP_IDEMPOTENCY_CACHE_ENABLED", True)):
        return None
    now = time.monotonic()
    if now < _http_redis_cooldown_until:
        return None
    url = _redis_url()
    if not url:
        return None
    if _http_redis is not None:
        return _http_redis
    try:
        import redis

        r = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        r.ping()
        _http_redis = r
        logger.info("MCP HTTP idempotency: using Redis")
        return _http_redis
    except Exception as exc:
        logger.warning("MCP HTTP idempotency: Redis unavailable (%s)", type(exc).__name__)
        _http_redis_cooldown_until = now + 30.0
        return None


def http_idempotency_cache_key(business_id: int, target_key: str, idem: str) -> str:
    pfx = str(getattr(settings, "MCP_HTTP_IDEMPOTENCY_REDIS_PREFIX", "sandhi:mcp_http_idemp:v1:") or "sandhi:mcp_http_idemp:v1:")
    h = hashlib.sha256(f"{int(business_id)}\0{target_key}\0{idem}".encode("utf-8")).hexdigest()
    return f"{pfx}{h}"


def should_cache_mcp_tool_result(out: Any) -> bool:
    if not isinstance(out, dict):
        return False
    if out.get("isError"):
        return False
    return True


def try_get_cached_tool_result(business_id: int, target_key: str, idem: str) -> Optional[Dict[str, Any]]:
    if not idem or not bool(getattr(settings, "MCP_HTTP_IDEMPOTENCY_CACHE_ENABLED", True)):
        return None
    r = get_http_idempotency_redis()
    if r is None:
        return None
    try:
        raw = r.get(http_idempotency_cache_key(business_id, target_key, idem))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("mcp http idempotency get failed", exc_info=True)
        return None


def store_cached_tool_result(business_id: int, target_key: str, idem: str, out: Dict[str, Any]) -> None:
    if not idem or not bool(getattr(settings, "MCP_HTTP_IDEMPOTENCY_CACHE_ENABLED", True)):
        return
    if not should_cache_mcp_tool_result(out):
        return
    r = get_http_idempotency_redis()
    if r is None:
        return
    try:
        ttl = int(getattr(settings, "MCP_HTTP_IDEMPOTENCY_TTL_SECONDS", 3600) or 3600)
        ttl = max(60, min(ttl, 86400))
        key = http_idempotency_cache_key(business_id, target_key, idem)
        r.setex(key, ttl, json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        logger.debug("mcp http idempotency setex failed", exc_info=True)
