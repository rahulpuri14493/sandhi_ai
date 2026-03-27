import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from core.config import settings


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple per-process sliding-window rate limiter.

    This is intentionally lightweight and protects key abuse-prone endpoints.
    For multi-instance production deployments, swap to Redis-backed limits.
    """

    def __init__(self, app):
        super().__init__(app)
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def _route_key_and_limit(self, request: Request) -> tuple[Optional[str], int]:
        path = request.url.path
        method = request.method.upper()

        if method == "POST" and path in ("/api/auth/login", "/api/auth/register"):
            return ("auth", settings.RATE_LIMIT_AUTH_PER_MINUTE)

        if method == "GET" and path.startswith("/api/agents"):
            return ("agents_reads", settings.RATE_LIMIT_AGENT_READS_PER_MINUTE)

        if method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api/jobs"):
            return ("jobs_mutations", settings.RATE_LIMIT_JOB_MUTATIONS_PER_MINUTE)

        return (None, 0)

    def _is_limited(self, bucket_key: str, limit_per_minute: int, now: float) -> bool:
        window_start = now - 60.0
        q = self._buckets[bucket_key]
        while q and q[0] < window_start:
            q.popleft()
        if len(q) >= limit_per_minute:
            return True
        q.append(now)
        return False

    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        route_key, limit_per_minute = self._route_key_and_limit(request)
        if route_key:
            ip = self._client_ip(request)
            bucket_key = f"{route_key}:{ip}"
            if self._is_limited(bucket_key, limit_per_minute, time.time()):
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "detail": "Rate limit exceeded. Please retry later.",
                        "route": route_key,
                        "limit_per_minute": limit_per_minute,
                    },
                    headers={"Retry-After": "60"},
                )

        return await call_next(request)

