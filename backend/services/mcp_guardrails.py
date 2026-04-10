import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

import httpx

from core.config import settings
from services.mcp_client import MCPJSONRPCError

import logging

logger = logging.getLogger(__name__)


@dataclass
class MCPErrorClassification:
    code: str
    retryable: bool
    detail: str


class MCPGuardrailError(RuntimeError):
    def __init__(self, code: str, detail: str, *, retryable: bool, cause: Optional[BaseException] = None):
        super().__init__(detail)
        self.code = code
        self.retryable = retryable
        self.cause = cause


class MCPInvocationGuardrails:
    """
    Process-local MCP invocation guardrails:
    - timeout bounding
    - retry classification with bounded backoff+jitter
    - circuit breaker per target
    - tenant/target concurrency + tenant fixed-window rate limit
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tenant_inflight: Dict[int, int] = {}
        self._target_inflight: Dict[str, int] = {}
        self._tenant_window: Dict[int, tuple[float, int]] = {}
        self._breakers: Dict[str, Dict[str, Any]] = {}
        self._redis_client: Any = None
        self._redis_init_failed = False

    @staticmethod
    def _redis_prefix() -> str:
        return (getattr(settings, "MCP_GUARDRAILS_REDIS_PREFIX", None) or "sandhi:mcp_guardrails:v1").strip()

    def _is_distributed_enabled(self) -> bool:
        return bool(getattr(settings, "MCP_GUARDRAILS_DISTRIBUTED_ENABLED", False))

    def _get_redis_client(self):
        if not self._is_distributed_enabled():
            return None
        if self._redis_init_failed:
            return None
        if self._redis_client is not None:
            return self._redis_client
        url = (getattr(settings, "MCP_GUARDRAILS_REDIS_URL", None) or "").strip()
        if not url:
            return None
        try:
            import redis  # lazy import
        except ModuleNotFoundError:
            self._redis_init_failed = True
            logger.warning("mcp_guardrails: redis package missing; falling back to process-local state")
            return None
        try:
            self._redis_client = redis.Redis.from_url(
                url,
                decode_responses=False,
                socket_timeout=float(getattr(settings, "MCP_GUARDRAILS_REDIS_SOCKET_TIMEOUT_SECONDS", 2.0) or 2.0),
                socket_connect_timeout=float(
                    getattr(settings, "MCP_GUARDRAILS_REDIS_CONNECT_TIMEOUT_SECONDS", 2.0) or 2.0
                ),
            )
            self._redis_client.ping()
            return self._redis_client
        except Exception:
            self._redis_init_failed = True
            logger.warning("mcp_guardrails: redis unavailable; falling back to process-local state")
            return None

    @staticmethod
    def _bounded_timeout(timeout_seconds: Optional[float]) -> float:
        default_t = float(getattr(settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0) or 60.0)
        max_t = float(getattr(settings, "MCP_TOOL_MAX_TIMEOUT_SECONDS", 300.0) or 300.0)
        t = float(timeout_seconds or default_t)
        t = max(1.0, t)
        if t > max_t:
            t = max_t
        return t

    @staticmethod
    def _classify_exception(exc: BaseException) -> MCPErrorClassification:
        if isinstance(exc, asyncio.TimeoutError):
            return MCPErrorClassification("mcp_timeout", True, "MCP call timed out")
        if isinstance(exc, httpx.TimeoutException):
            return MCPErrorClassification("mcp_timeout", True, "MCP upstream timeout")
        if isinstance(exc, MCPJSONRPCError):
            rpc_code = exc.rpc_code
            data = exc.rpc_data or {}
            if rpc_code in (-32602, -32600):
                return MCPErrorClassification("mcp_tool_validation_failed", False, str(exc))
            if rpc_code in (-32002,):
                return MCPErrorClassification("mcp_rate_limited", True, str(exc))
            if rpc_code in (-32001, -32098, -32099):
                return MCPErrorClassification("mcp_upstream_unavailable", True, str(exc))
            # Data-driven classification fallback.
            status = data.get("status") if isinstance(data, dict) else None
            try:
                status_i = int(status) if status is not None else None
            except (TypeError, ValueError):
                status_i = None
            if status_i == 429:
                return MCPErrorClassification("mcp_rate_limited", True, str(exc))
            if status_i is not None and 500 <= status_i <= 599:
                return MCPErrorClassification("mcp_upstream_unavailable", True, str(exc))
            if status_i is not None and 400 <= status_i <= 499:
                return MCPErrorClassification("mcp_tool_validation_failed", False, str(exc))
            return MCPErrorClassification("mcp_unknown", False, str(exc))
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code if exc.response is not None else None
            if code == 429:
                return MCPErrorClassification("mcp_rate_limited", True, "MCP upstream rate-limited request")
            if code in {408, 409, 425}:
                return MCPErrorClassification("mcp_upstream_unavailable", True, f"MCP transient HTTP status {code}")
            if code is not None and 500 <= int(code) <= 599:
                return MCPErrorClassification("mcp_upstream_unavailable", True, f"MCP upstream HTTP status {code}")
            if code is not None and 400 <= int(code) <= 499:
                return MCPErrorClassification("mcp_tool_validation_failed", False, f"MCP client/tool HTTP status {code}")
            return MCPErrorClassification("mcp_upstream_unavailable", True, "MCP HTTP status error")
        if isinstance(exc, httpx.TransportError):
            return MCPErrorClassification("mcp_upstream_unavailable", True, "MCP transport failure")

        msg = str(exc or "").lower()
        if "timeout" in msg or "timed out" in msg:
            return MCPErrorClassification("mcp_timeout", True, "MCP timeout")
        if "too many requests" in msg or "rate limit" in msg or "429" in msg:
            return MCPErrorClassification("mcp_rate_limited", True, "MCP upstream throttled")
        if (
            "service unavailable" in msg
            or "connection reset" in msg
            or "connection refused" in msg
            or "temporary failure" in msg
            or "gateway" in msg
            or " 5" in msg
        ):
            return MCPErrorClassification("mcp_upstream_unavailable", True, "MCP upstream unavailable")
        if "validation" in msg or "invalid" in msg or "bad request" in msg or "unauthorized" in msg:
            return MCPErrorClassification("mcp_tool_validation_failed", False, "MCP request validation/auth failed")
        raw = str(exc) if exc is not None else ""
        raw = raw.strip()
        # Preserve message verbatim so upstream tests and operators see the true cause.
        if raw:
            return MCPErrorClassification("mcp_unknown", False, raw)
        return MCPErrorClassification("mcp_unknown", False, type(exc).__name__)

    def _redis_counter_key(self, kind: str, business_id: int, target_key: str) -> str:
        p = self._redis_prefix()
        if kind == "tenant":
            return f"{p}:inflight:tenant:{business_id}"
        if kind == "target":
            return f"{p}:inflight:target:{target_key}"
        return f"{p}:inflight:unknown:{business_id}:{target_key}"

    def _redis_rate_key(self, business_id: int, now_monotonic: float) -> str:
        p = self._redis_prefix()
        # time.monotonic not epoch, but minute bucketing consistency per process is enough for key reuse window.
        minute_bucket = int(now_monotonic // 60)
        return f"{p}:rate:tenant:{business_id}:{minute_bucket}"

    async def _acquire_quota_distributed(self, business_id: int, target_key: str) -> bool:
        r = self._get_redis_client()
        if r is None:
            return False
        tenant_limit = int(getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
        target_limit = int(getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
        rpm_limit = int(getattr(settings, "MCP_TENANT_RATE_LIMIT_PER_MINUTE", 0) or 0)
        wait_s = float(getattr(settings, "MCP_CONCURRENCY_WAIT_SECONDS", 10.0) or 10.0)
        counter_ttl = int(getattr(settings, "MCP_GUARDRAILS_COUNTER_TTL_SECONDS", 120) or 120)
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            now = time.monotonic()
            # Rate limit first.
            if rpm_limit > 0:
                rate_key = self._redis_rate_key(business_id, now)
                count = int(r.incr(rate_key))
                if count == 1:
                    r.expire(rate_key, 60)
                if count > rpm_limit:
                    raise MCPGuardrailError("mcp_rate_limited", f"Tenant {business_id} exceeded MCP rate limit", retryable=True)

            tenant_key = self._redis_counter_key("tenant", business_id, target_key)
            target_counter_key = self._redis_counter_key("target", business_id, target_key)
            tenant_ok = True
            target_ok = True
            tenant_val = 0
            target_val = 0
            if tenant_limit > 0:
                tenant_val = int(r.incr(tenant_key))
                if tenant_val == 1:
                    r.expire(tenant_key, counter_ttl)
                tenant_ok = tenant_val <= tenant_limit
            if target_limit > 0:
                target_val = int(r.incr(target_counter_key))
                if target_val == 1:
                    r.expire(target_counter_key, counter_ttl)
                target_ok = target_val <= target_limit
            if tenant_ok and target_ok:
                return True
            # rollback this attempt increments
            if tenant_limit > 0 and tenant_val > 0:
                r.decr(tenant_key)
            if target_limit > 0 and target_val > 0:
                r.decr(target_counter_key)
            if now >= deadline:
                raise MCPGuardrailError(
                    "mcp_quota_exceeded",
                    f"MCP concurrency saturated for tenant={business_id} target={target_key}",
                    retryable=True,
                )
            await asyncio.sleep(min(0.05, max(0.005, deadline - now)))

    async def _acquire_quota(self, business_id: int, target_key: str) -> None:
        # Prefer distributed guardrails when configured and Redis is reachable.
        used_distributed = await self._acquire_quota_distributed(business_id, target_key)
        if used_distributed:
            return
        tenant_limit = int(getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
        target_limit = int(getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
        rpm_limit = int(getattr(settings, "MCP_TENANT_RATE_LIMIT_PER_MINUTE", 0) or 0)
        wait_s = float(getattr(settings, "MCP_CONCURRENCY_WAIT_SECONDS", 10.0) or 10.0)
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            now = time.monotonic()
            async with self._lock:
                if rpm_limit > 0:
                    start, count = self._tenant_window.get(business_id, (now, 0))
                    if (now - start) >= 60.0:
                        start, count = now, 0
                    if count >= rpm_limit:
                        raise MCPGuardrailError(
                            "mcp_rate_limited",
                            f"Tenant {business_id} exceeded MCP rate limit",
                            retryable=True,
                        )

                cur_tenant = int(self._tenant_inflight.get(business_id, 0))
                cur_target = int(self._target_inflight.get(target_key, 0))
                tenant_ok = (tenant_limit <= 0) or (cur_tenant < tenant_limit)
                target_ok = (target_limit <= 0) or (cur_target < target_limit)
                if tenant_ok and target_ok:
                    if rpm_limit > 0:
                        start, count = self._tenant_window.get(business_id, (now, 0))
                        self._tenant_window[business_id] = (start, count + 1)
                    if tenant_limit > 0:
                        self._tenant_inflight[business_id] = cur_tenant + 1
                    if target_limit > 0:
                        self._target_inflight[target_key] = cur_target + 1
                    return

            if now >= deadline:
                raise MCPGuardrailError(
                    "mcp_quota_exceeded",
                    f"MCP concurrency saturated for tenant={business_id} target={target_key}",
                    retryable=True,
                )
            await asyncio.sleep(min(0.05, max(0.005, deadline - now)))

    async def _release_quota(self, business_id: int, target_key: str) -> None:
        r = self._get_redis_client()
        if r is not None:
            tenant_limit = int(getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
            target_limit = int(getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
            if tenant_limit > 0:
                try:
                    r.decr(self._redis_counter_key("tenant", business_id, target_key))
                except Exception:
                    pass
            if target_limit > 0:
                try:
                    r.decr(self._redis_counter_key("target", business_id, target_key))
                except Exception:
                    pass
            return
        tenant_limit = int(getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
        target_limit = int(getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
        if tenant_limit <= 0 and target_limit <= 0:
            return
        async with self._lock:
            if tenant_limit > 0:
                t = max(0, int(self._tenant_inflight.get(business_id, 0)) - 1)
                if t == 0:
                    self._tenant_inflight.pop(business_id, None)
                else:
                    self._tenant_inflight[business_id] = t
            if target_limit > 0:
                tg = max(0, int(self._target_inflight.get(target_key, 0)) - 1)
                if tg == 0:
                    self._target_inflight.pop(target_key, None)
                else:
                    self._target_inflight[target_key] = tg

    async def _circuit_preflight(self, target_key: str) -> None:
        r = self._get_redis_client()
        if r is not None:
            p = self._redis_prefix()
            key = f"{p}:breaker:{target_key}"
            open_secs = float(getattr(settings, "MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 30.0) or 30.0)
            half_open_max = int(getattr(settings, "MCP_CIRCUIT_BREAKER_HALF_OPEN_MAX_PROBES", 1) or 1)
            now = time.monotonic()
            raw_state = r.hget(key, "state")
            state = raw_state.decode() if isinstance(raw_state, (bytes, bytearray)) else (raw_state or "closed")
            if state == "open":
                opened_until_raw = r.hget(key, "opened_until")
                try:
                    opened_until = float(opened_until_raw.decode() if isinstance(opened_until_raw, (bytes, bytearray)) else opened_until_raw)
                except Exception:
                    opened_until = 0.0
                if now < opened_until:
                    raise MCPGuardrailError("mcp_circuit_open", f"Circuit open for target {target_key}", retryable=True)
                r.hset(key, mapping={"state": "half_open", "half_open_probes": 0, "opened_until": now + open_secs})
                r.expire(key, int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300))
                state = "half_open"
            if state == "half_open":
                probes_raw = r.hget(key, "half_open_probes")
                try:
                    probes = int(probes_raw.decode() if isinstance(probes_raw, (bytes, bytearray)) else probes_raw or 0)
                except Exception:
                    probes = 0
                if probes >= max(1, half_open_max):
                    raise MCPGuardrailError("mcp_circuit_open", f"Circuit half-open probe limit reached for {target_key}", retryable=True)
                r.hincrby(key, "half_open_probes", 1)
            return
        open_secs = float(getattr(settings, "MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 30.0) or 30.0)
        half_open_max = int(getattr(settings, "MCP_CIRCUIT_BREAKER_HALF_OPEN_MAX_PROBES", 1) or 1)
        now = time.monotonic()
        async with self._lock:
            st = self._breakers.get(target_key)
            if not st:
                self._breakers[target_key] = {"state": "closed", "failures": 0, "opened_until": 0.0, "half_open_probes": 0}
                return
            state = st.get("state", "closed")
            if state == "open":
                opened_until = float(st.get("opened_until", 0.0) or 0.0)
                if now < opened_until:
                    raise MCPGuardrailError("mcp_circuit_open", f"Circuit open for target {target_key}", retryable=True)
                st["state"] = "half_open"
                st["half_open_probes"] = 0
                st["opened_until"] = now + open_secs
                state = "half_open"
            if state == "half_open":
                probes = int(st.get("half_open_probes", 0) or 0)
                if probes >= max(1, half_open_max):
                    raise MCPGuardrailError("mcp_circuit_open", f"Circuit half-open probe limit reached for {target_key}", retryable=True)
                st["half_open_probes"] = probes + 1

    async def _circuit_record_success(self, target_key: str) -> None:
        r = self._get_redis_client()
        if r is not None:
            p = self._redis_prefix()
            key = f"{p}:breaker:{target_key}"
            r.hset(key, mapping={"state": "closed", "failures": 0, "half_open_probes": 0})
            r.expire(key, int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300))
            return
        async with self._lock:
            st = self._breakers.get(target_key)
            if not st:
                self._breakers[target_key] = {"state": "closed", "failures": 0, "opened_until": 0.0, "half_open_probes": 0}
                return
            st["state"] = "closed"
            st["failures"] = 0
            st["half_open_probes"] = 0

    async def _circuit_record_failure(self, target_key: str, *, retryable: bool) -> None:
        r = self._get_redis_client()
        if r is not None:
            p = self._redis_prefix()
            key = f"{p}:breaker:{target_key}"
            threshold = int(getattr(settings, "MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5) or 5)
            open_secs = float(getattr(settings, "MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 30.0) or 30.0)
            now = time.monotonic()
            raw_state = r.hget(key, "state")
            state = raw_state.decode() if isinstance(raw_state, (bytes, bytearray)) else (raw_state or "closed")
            if state == "half_open" and retryable:
                r.hset(key, mapping={"state": "open", "opened_until": now + open_secs, "half_open_probes": 0})
                r.hincrby(key, "failures", 1)
                r.expire(key, int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300))
                return
            if not retryable:
                if state == "half_open":
                    r.hset(key, mapping={"state": "closed", "half_open_probes": 0})
                return
            failures = int(r.hincrby(key, "failures", 1))
            if failures >= max(1, threshold):
                r.hset(key, mapping={"state": "open", "opened_until": now + open_secs, "half_open_probes": 0})
            r.expire(key, int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300))
            return
        threshold = int(getattr(settings, "MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5) or 5)
        open_secs = float(getattr(settings, "MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 30.0) or 30.0)
        now = time.monotonic()
        async with self._lock:
            st = self._breakers.get(target_key)
            if not st:
                st = {"state": "closed", "failures": 0, "opened_until": 0.0, "half_open_probes": 0}
                self._breakers[target_key] = st
            state = st.get("state", "closed")
            if state == "half_open" and retryable:
                st["state"] = "open"
                st["opened_until"] = now + open_secs
                st["failures"] = max(1, int(st.get("failures", 0) or 0) + 1)
                st["half_open_probes"] = 0
                return
            if not retryable:
                if state == "half_open":
                    st["state"] = "closed"
                    st["half_open_probes"] = 0
                return
            failures = int(st.get("failures", 0) or 0) + 1
            st["failures"] = failures
            if failures >= max(1, threshold):
                st["state"] = "open"
                st["opened_until"] = now + open_secs
                st["half_open_probes"] = 0

    async def call_tool_with_guardrails(
        self,
        *,
        business_id: int,
        target_key: str,
        timeout_seconds: Optional[float],
        execute_call: Callable[[float], Awaitable[Dict[str, Any]]],
        operation_class: Literal["read_like", "write_like"] = "read_like",
    ) -> Dict[str, Any]:
        legacy_attempts = int(getattr(settings, "MCP_INVOCATION_MAX_ATTEMPTS", 3) or 3)
        legacy_base_delay = float(getattr(settings, "MCP_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.25) or 0.25)
        legacy_max_delay = float(getattr(settings, "MCP_INVOCATION_RETRY_MAX_DELAY_SECONDS", 3.0) or 3.0)
        legacy_jitter = float(getattr(settings, "MCP_INVOCATION_RETRY_JITTER_SECONDS", 0.15) or 0.15)
        if operation_class == "write_like":
            attempts = int(getattr(settings, "MCP_WRITE_INVOCATION_MAX_ATTEMPTS", 0) or 0)
            if attempts <= 0:
                attempts = legacy_attempts
            base_delay = float(getattr(settings, "MCP_WRITE_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0) or 0.0)
            max_delay = float(getattr(settings, "MCP_WRITE_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0) or 0.0)
            jitter = float(getattr(settings, "MCP_WRITE_INVOCATION_RETRY_JITTER_SECONDS", 0.0) or 0.0)
            if base_delay <= 0.0:
                base_delay = legacy_base_delay
            if max_delay <= 0.0:
                max_delay = legacy_max_delay
            if jitter <= 0.0:
                jitter = legacy_jitter
        else:
            attempts = int(getattr(settings, "MCP_READ_INVOCATION_MAX_ATTEMPTS", 0) or 0)
            if attempts <= 0:
                attempts = legacy_attempts
            base_delay = float(getattr(settings, "MCP_READ_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0) or 0.0)
            max_delay = float(getattr(settings, "MCP_READ_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0) or 0.0)
            jitter = float(getattr(settings, "MCP_READ_INVOCATION_RETRY_JITTER_SECONDS", 0.0) or 0.0)
            if base_delay <= 0.0:
                base_delay = legacy_base_delay
            if max_delay <= 0.0:
                max_delay = legacy_max_delay
            if jitter <= 0.0:
                jitter = legacy_jitter
        attempts = max(1, attempts)
        bounded_timeout = self._bounded_timeout(timeout_seconds)
        last_exc: Optional[BaseException] = None

        for attempt in range(1, attempts + 1):
            acquired_quota = False
            await self._circuit_preflight(target_key)
            await self._acquire_quota(business_id, target_key)
            acquired_quota = True
            logger.info(
                "mcp_guardrail_event event=admitted business_id=%s target=%s op=%s attempt=%s/%s timeout=%.2f",
                business_id,
                target_key,
                operation_class,
                attempt,
                attempts,
                bounded_timeout,
            )
            try:
                out = await execute_call(bounded_timeout)
                await self._circuit_record_success(target_key)
                logger.info(
                    "mcp_guardrail_event event=success business_id=%s target=%s op=%s attempt=%s/%s",
                    business_id,
                    target_key,
                    operation_class,
                    attempt,
                    attempts,
                )
                return out
            except MCPGuardrailError as ge:
                # Quota/circuit preflight style errors can bubble directly.
                await self._circuit_record_failure(target_key, retryable=ge.retryable)
                last_exc = ge
                logger.warning(
                    "mcp_guardrail_event event=guardrail_error business_id=%s target=%s op=%s code=%s retryable=%s attempt=%s/%s detail=%s",
                    business_id,
                    target_key,
                    operation_class,
                    ge.code,
                    ge.retryable,
                    attempt,
                    attempts,
                    str(ge),
                )
                if attempt < attempts and ge.retryable:
                    sleep_s = min(max_delay, base_delay * (2 ** (attempt - 1))) + (random.random() * max(0.0, jitter))
                    logger.info(
                        "mcp_guardrail_event event=retry_backoff business_id=%s target=%s op=%s code=%s sleep_seconds=%.3f",
                        business_id,
                        target_key,
                        operation_class,
                        ge.code,
                        sleep_s,
                    )
                    await asyncio.sleep(sleep_s)
                    continue
                raise
            except Exception as exc:
                cls = self._classify_exception(exc)
                await self._circuit_record_failure(target_key, retryable=cls.retryable)
                last_exc = exc
                logger.warning(
                    "mcp_guardrail_event event=call_error business_id=%s target=%s op=%s code=%s retryable=%s attempt=%s/%s detail=%s",
                    business_id,
                    target_key,
                    operation_class,
                    cls.code,
                    cls.retryable,
                    attempt,
                    attempts,
                    cls.detail,
                )
                if attempt < attempts and cls.retryable:
                    sleep_s = min(max_delay, base_delay * (2 ** (attempt - 1))) + (random.random() * max(0.0, jitter))
                    logger.info(
                        "mcp_guardrail_event event=retry_backoff business_id=%s target=%s op=%s code=%s sleep_seconds=%.3f",
                        business_id,
                        target_key,
                        operation_class,
                        cls.code,
                        sleep_s,
                    )
                    await asyncio.sleep(sleep_s)
                    continue
                raise MCPGuardrailError(cls.code, cls.detail, retryable=cls.retryable, cause=exc) from exc
            finally:
                if acquired_quota:
                    await self._release_quota(business_id, target_key)

        if isinstance(last_exc, MCPGuardrailError):
            raise last_exc
        if last_exc is not None:
            cls = self._classify_exception(last_exc)
            raise MCPGuardrailError(cls.code, cls.detail, retryable=cls.retryable, cause=last_exc) from last_exc
        raise MCPGuardrailError("mcp_unknown", "MCP guardrail failed without error detail", retryable=False)


_MCP_GUARDRAILS: Optional[MCPInvocationGuardrails] = None


def get_mcp_guardrails() -> MCPInvocationGuardrails:
    global _MCP_GUARDRAILS
    if _MCP_GUARDRAILS is None:
        _MCP_GUARDRAILS = MCPInvocationGuardrails()
    return _MCP_GUARDRAILS
