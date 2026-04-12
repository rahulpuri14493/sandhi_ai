"""
Short TTL cache for hot read-only platform tool paths (reduces Graph/Slack latency under burst).

Disable with MCP_PLATFORM_READ_CACHE_TTL_SECONDS=0 (default 10 seconds, max 120).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable


_lock = threading.Lock()
_store: dict[str, tuple[float, str]] = {}


def cache_ttl_seconds() -> float:
    try:
        v = float(os.environ.get("MCP_PLATFORM_READ_CACHE_TTL_SECONDS", "10") or "10")
    except (TypeError, ValueError):
        v = 10.0
    return max(0.0, min(v, 120.0))


def get_cached_or_run(cache_key: str, producer: Callable[[], str]) -> str:
    ttl = cache_ttl_seconds()
    if ttl <= 0:
        return producer()
    now = time.monotonic()
    with _lock:
        ent = _store.get(cache_key)
        if ent is not None:
            expires, val = ent
            if now < expires:
                return val
    value = producer()
    with _lock:
        _store[cache_key] = (now + ttl, value)
        if len(_store) > 2000:
            for k, (exp, _) in list(_store.items()):
                if exp < now:
                    del _store[k]
    return value
