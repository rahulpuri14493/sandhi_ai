"""
Shared synchronous httpx client for platform MCP outbound HTTP.

Under heavy load, creating a new ``httpx.Client`` per request discards TCP/TLS
connection pools and increases latency. A process-wide client reuses keep-alive
connections per destination host (Graph, Gmail, Chroma embed, backend internal, etc.).
"""
from __future__ import annotations

import atexit
import threading
from typing import Optional

import httpx

# Tuned for many concurrent tool calls; httpx pools per (scheme, host, port).
_POOL_LIMITS = httpx.Limits(max_keepalive_connections=64, max_connections=256)

_client: Optional[httpx.Client] = None
_lock = threading.Lock()


def get_sync_http_client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                limits=_POOL_LIMITS,
                follow_redirects=True,
            )
        return _client


def close_sync_http_client() -> None:
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


atexit.register(close_sync_http_client)
