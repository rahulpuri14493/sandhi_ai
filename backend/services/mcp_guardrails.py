import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, cast

import httpx

from core.config import settings
from services.mcp_client import MCPJSONRPCError
from services.mcp_metrics import increment_event, observe_duration

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
        self._fair_queue_local: Dict[str, list[str]] = {}
        self._redis_client: Any = None
        self._redis_init_failed = False
        self._redis_retry_after_monotonic: float = 0.0
        self._redis_error_logged = False

    _LUA_ADMIT = """
local tenant_key = KEYS[1]
local target_key = KEYS[2]
local rate_key = KEYS[3]
local tenant_limit = tonumber(ARGV[1]) or 0
local target_limit = tonumber(ARGV[2]) or 0
local rpm_limit = tonumber(ARGV[3]) or 0
local counter_ttl = tonumber(ARGV[4]) or 120

if rpm_limit > 0 then
  local rate_count = redis.call("INCR", rate_key)
  if rate_count == 1 then redis.call("EXPIRE", rate_key, 60) end
  if rate_count > rpm_limit then return -1 end
end

local tenant_val = 0
local target_val = 0

if tenant_limit > 0 then
  tenant_val = redis.call("INCR", tenant_key)
  if tenant_val == 1 then redis.call("EXPIRE", tenant_key, counter_ttl) end
end
if target_limit > 0 then
  target_val = redis.call("INCR", target_key)
  if target_val == 1 then redis.call("EXPIRE", target_key, counter_ttl) end
end

if (tenant_limit <= 0 or tenant_val <= tenant_limit) and (target_limit <= 0 or target_val <= target_limit) then
  return 1
end

if tenant_limit > 0 and tenant_val > 0 then redis.call("DECR", tenant_key) end
if target_limit > 0 and target_val > 0 then redis.call("DECR", target_key) end
return 0
"""

    _LUA_CIRCUIT_PREFLIGHT = """
local key = KEYS[1]
local now = tonumber(ARGV[1]) or 0
local open_secs = tonumber(ARGV[2]) or 30
local half_open_max = tonumber(ARGV[3]) or 1
local ttl = tonumber(ARGV[4]) or 300

local state = redis.call("HGET", key, "state")
if not state then
  redis.call("HSET", key, "state", "closed", "failures", 0, "opened_until", 0, "half_open_probes", 0)
  redis.call("EXPIRE", key, ttl)
  return 1
end

if state == "open" then
  local opened_until = tonumber(redis.call("HGET", key, "opened_until") or "0")
  if now < opened_until then return 0 end
  redis.call("HSET", key, "state", "half_open", "half_open_probes", 0, "opened_until", now + open_secs)
  redis.call("EXPIRE", key, ttl)
end

state = redis.call("HGET", key, "state")
if state == "half_open" then
  local probes = tonumber(redis.call("HGET", key, "half_open_probes") or "0")
  if probes >= half_open_max then return 0 end
  redis.call("HINCRBY", key, "half_open_probes", 1)
  redis.call("EXPIRE", key, ttl)
end
return 1
"""

    _LUA_FAIR_QUEUE_NEXT = """
local queue_key = KEYS[1]
local token = ARGV[1]
local now = tonumber(ARGV[2]) or 0
local stale_before = tonumber(ARGV[3]) or 0
redis.call("ZREMRANGEBYSCORE", queue_key, 0, stale_before)
local head = redis.call("ZRANGE", queue_key, 0, 0)
if not head or #head == 0 then return 0 end
if head[1] == token then return 1 end
return 0
"""

    @staticmethod
    def _redis_prefix() -> str:
        return (getattr(settings, "MCP_GUARDRAILS_REDIS_PREFIX", None) or "sandhi:mcp_guardrails:v1").strip()

    def _is_distributed_enabled(self) -> bool:
        return bool(getattr(settings, "MCP_GUARDRAILS_DISTRIBUTED_ENABLED", False))

    def _get_redis_client(self):
        if not self._is_distributed_enabled():
            return None
        now_mono = time.monotonic()
        if self._redis_init_failed:
            if now_mono < float(self._redis_retry_after_monotonic or 0.0):
                return None
            # retry window reached; allow re-init attempts
            self._redis_init_failed = False
        if self._redis_client is not None:
            return self._redis_client
        url = (getattr(settings, "MCP_GUARDRAILS_REDIS_URL", None) or "").strip()
        if not url:
            return None
        try:
            import redis  # lazy import
        except ModuleNotFoundError:
            self._redis_init_failed = True
            retry_s = float(getattr(settings, "MCP_GUARDRAILS_REDIS_RETRY_SECONDS", 30.0) or 30.0)
            self._redis_retry_after_monotonic = time.monotonic() + max(1.0, retry_s)
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
            retry_s = float(getattr(settings, "MCP_GUARDRAILS_REDIS_RETRY_SECONDS", 30.0) or 30.0)
            self._redis_retry_after_monotonic = time.monotonic() + max(1.0, retry_s)
            logger.warning("mcp_guardrails: redis unavailable; falling back to process-local state")
            return None

    def _disable_distributed_due_error(self, context: str) -> None:
        self._redis_client = None
        self._redis_init_failed = True
        retry_s = float(getattr(settings, "MCP_GUARDRAILS_REDIS_RETRY_SECONDS", 30.0) or 30.0)
        self._redis_retry_after_monotonic = time.monotonic() + max(1.0, retry_s)
        if not self._redis_error_logged:
            logger.warning("mcp_guardrails: disabling distributed mode after redis error in %s", context)
            self._redis_error_logged = True

    @staticmethod
    def _parse_json_map(raw: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(raw or "{}")
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {}

    def _resolve_runtime_policy(
        self,
        *,
        operation_class: str,
        tenant_tier: Optional[str],
        tool_name: Optional[str],
        write_retry_safe: Optional[bool],
    ) -> Dict[str, Any]:
        tier_name = str(tenant_tier or getattr(settings, "MCP_DEFAULT_BUSINESS_TIER", "bronze") or "bronze").strip().lower()
        tier_map = self._parse_json_map(getattr(settings, "MCP_TENANT_TIER_POLICY_JSON", "{}"))
        tool_map = self._parse_json_map(getattr(settings, "MCP_TOOL_POLICY_JSON", "{}"))
        tool_key = str(tool_name or "").strip()
        policy: Dict[str, Any] = {}
        if isinstance(tier_map.get(tier_name), dict):
            policy.update(tier_map[tier_name])
        if tool_key and isinstance(tool_map.get(tool_key), dict):
            policy.update(tool_map[tool_key])
        if write_retry_safe is not None:
            policy["write_retry_safe"] = bool(write_retry_safe)
        if "operation_class" not in policy:
            policy["operation_class"] = operation_class
        return policy

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
        if kind == "queue":
            return f"{p}:queue:target:{target_key}"
        return f"{p}:inflight:unknown:{business_id}:{target_key}"

    def _redis_rate_key(self, business_id: int, now_epoch_seconds: float) -> str:
        p = self._redis_prefix()
        minute_bucket = int(now_epoch_seconds // 60)
        return f"{p}:rate:tenant:{business_id}:{minute_bucket}"

    async def _acquire_quota_distributed(self, business_id: int, target_key: str, policy: Dict[str, Any]) -> bool:
        r = self._get_redis_client()
        if r is None:
            return False
        tenant_limit = int(policy.get("tenant_max_concurrent_calls") or getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
        target_limit = int(policy.get("target_max_concurrent_calls") or getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
        rpm_limit = int(policy.get("tenant_rate_limit_per_minute") or getattr(settings, "MCP_TENANT_RATE_LIMIT_PER_MINUTE", 0) or 0)
        wait_s = float(getattr(settings, "MCP_CONCURRENCY_WAIT_SECONDS", 10.0) or 10.0)
        counter_ttl = int(getattr(settings, "MCP_GUARDRAILS_COUNTER_TTL_SECONDS", 120) or 120)
        fair_enabled = bool(getattr(settings, "MCP_GUARDRAILS_FAIR_QUEUE_ENABLED", False))
        fair_stale = float(getattr(settings, "MCP_GUARDRAILS_FAIR_QUEUE_STALE_SECONDS", 30.0) or 30.0)
        queue_key = self._redis_counter_key("queue", business_id, target_key)
        queue_token = f"{time.time():.6f}:{uuid.uuid4().hex}"
        enqueued = False
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            now = time.monotonic()
            epoch_now = time.time()
            if fair_enabled:
                if not enqueued:
                    try:
                        r.zadd(queue_key, {queue_token: epoch_now})
                        enqueued = True
                    except Exception:
                        self._disable_distributed_due_error("fair_queue_enqueue")
                        return False
                try:
                    can_try = int(r.eval(self._LUA_FAIR_QUEUE_NEXT, 1, queue_key, queue_token, float(epoch_now), float(epoch_now - fair_stale)))
                except Exception:
                    self._disable_distributed_due_error("fair_queue_next")
                    return False
                if can_try != 1:
                    if now >= deadline:
                        try:
                            r.zrem(queue_key, queue_token)
                        except Exception:
                            pass
                        raise MCPGuardrailError(
                            "mcp_quota_exceeded",
                            f"MCP concurrency saturated for tenant={business_id} target={target_key}",
                            retryable=True,
                        )
                    await asyncio.sleep(min(0.05, max(0.005, deadline - now)))
                    continue
            tenant_key = self._redis_counter_key("tenant", business_id, target_key)
            target_counter_key = self._redis_counter_key("target", business_id, target_key)
            rate_key = self._redis_rate_key(business_id, epoch_now)
            try:
                admit_code = int(
                    r.eval(
                        self._LUA_ADMIT,
                        3,
                        tenant_key,
                        target_counter_key,
                        rate_key,
                        int(tenant_limit),
                        int(target_limit),
                        int(rpm_limit),
                        int(counter_ttl),
                    )
                )
            except Exception:
                self._disable_distributed_due_error("quota_admission")
                return False
            if admit_code == 1:
                if fair_enabled:
                    try:
                        r.zrem(queue_key, queue_token)
                    except Exception:
                        pass
                return True
            if admit_code == -1:
                if fair_enabled:
                    try:
                        r.zrem(queue_key, queue_token)
                    except Exception:
                        pass
                raise MCPGuardrailError("mcp_rate_limited", f"Tenant {business_id} exceeded MCP rate limit", retryable=True)
            if now >= deadline:
                if fair_enabled:
                    try:
                        r.zrem(queue_key, queue_token)
                    except Exception:
                        pass
                raise MCPGuardrailError(
                    "mcp_quota_exceeded",
                    f"MCP concurrency saturated for tenant={business_id} target={target_key}",
                    retryable=True,
                )
            await asyncio.sleep(min(0.05, max(0.005, deadline - now)))

    async def _acquire_quota(self, business_id: int, target_key: str, policy: Dict[str, Any]) -> None:
        # Prefer distributed guardrails when configured and Redis is reachable.
        used_distributed = await self._acquire_quota_distributed(business_id, target_key, policy)
        if used_distributed:
            return
        tenant_limit = int(policy.get("tenant_max_concurrent_calls") or getattr(settings, "MCP_TENANT_MAX_CONCURRENT_CALLS", 0) or 0)
        target_limit = int(policy.get("target_max_concurrent_calls") or getattr(settings, "MCP_TARGET_MAX_CONCURRENT_CALLS", 0) or 0)
        rpm_limit = int(policy.get("tenant_rate_limit_per_minute") or getattr(settings, "MCP_TENANT_RATE_LIMIT_PER_MINUTE", 0) or 0)
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
            breaker_ttl = int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300)
            try:
                admitted = int(
                    r.eval(
                        self._LUA_CIRCUIT_PREFLIGHT,
                        1,
                        key,
                        float(now),
                        float(open_secs),
                        max(1, int(half_open_max)),
                        int(breaker_ttl),
                    )
                )
            except Exception:
                self._disable_distributed_due_error("circuit_preflight")
                admitted = 1
            if admitted != 1:
                raise MCPGuardrailError("mcp_circuit_open", f"Circuit open for target {target_key}", retryable=True)
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
            now = time.monotonic()
            window_s = float(getattr(settings, "MCP_CIRCUIT_BREAKER_WINDOW_SECONDS", 60.0) or 60.0)
            start = float(r.hget(key, "window_start") or 0.0)
            if start <= 0.0 or (now - start) >= window_s:
                start = now
                r.hset(key, mapping={"window_start": start, "window_total": 0, "window_failures": 0})
            r.hset(key, mapping={"state": "closed", "failures": 0, "half_open_probes": 0})
            r.hincrby(key, "window_total", 1)
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
            now = time.monotonic()
            window_s = float(getattr(settings, "MCP_CIRCUIT_BREAKER_WINDOW_SECONDS", 60.0) or 60.0)
            start = float(st.get("window_start", 0.0) or 0.0)
            if start <= 0.0 or (now - start) >= window_s:
                st["window_start"] = now
                st["window_total"] = 0
                st["window_failures"] = 0
            st["window_total"] = int(st.get("window_total", 0) or 0) + 1

    async def _circuit_record_failure(self, target_key: str, *, retryable: bool) -> None:
        r = self._get_redis_client()
        if r is not None:
            p = self._redis_prefix()
            key = f"{p}:breaker:{target_key}"
            threshold = int(getattr(settings, "MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5) or 5)
            min_samples = int(getattr(settings, "MCP_CIRCUIT_BREAKER_MIN_SAMPLES", 10) or 10)
            error_rate_threshold = float(getattr(settings, "MCP_CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD", 0.5) or 0.5)
            window_s = float(getattr(settings, "MCP_CIRCUIT_BREAKER_WINDOW_SECONDS", 60.0) or 60.0)
            open_secs = float(getattr(settings, "MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 30.0) or 30.0)
            now = time.monotonic()
            raw_state = r.hget(key, "state")
            state = raw_state.decode() if isinstance(raw_state, (bytes, bytearray)) else (raw_state or "closed")
            win_start_raw = r.hget(key, "window_start")
            try:
                win_start = float(win_start_raw.decode() if isinstance(win_start_raw, (bytes, bytearray)) else (win_start_raw or 0.0))
            except Exception:
                win_start = 0.0
            if win_start <= 0.0 or (now - win_start) >= window_s:
                r.hset(key, mapping={"window_start": now, "window_total": 0, "window_failures": 0})
            if state == "half_open" and retryable:
                r.hset(key, mapping={"state": "open", "opened_until": now + open_secs, "half_open_probes": 0})
                r.hincrby(key, "failures", 1)
                r.hincrby(key, "window_total", 1)
                r.hincrby(key, "window_failures", 1)
                r.expire(key, int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300))
                return
            if not retryable:
                if state == "half_open":
                    r.hset(key, mapping={"state": "closed", "half_open_probes": 0})
                r.hincrby(key, "window_total", 1)
                return
            failures = int(r.hincrby(key, "failures", 1))
            window_total = int(r.hincrby(key, "window_total", 1))
            window_failures = int(r.hincrby(key, "window_failures", 1))
            error_rate_open = window_total >= max(1, min_samples) and (window_failures / max(1, window_total)) >= error_rate_threshold
            if failures >= max(1, threshold) or error_rate_open:
                r.hset(key, mapping={"state": "open", "opened_until": now + open_secs, "half_open_probes": 0})
            r.expire(key, int(getattr(settings, "MCP_GUARDRAILS_BREAKER_TTL_SECONDS", 300) or 300))
            return
        threshold = int(getattr(settings, "MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5) or 5)
        open_secs = float(getattr(settings, "MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 30.0) or 30.0)
        min_samples = int(getattr(settings, "MCP_CIRCUIT_BREAKER_MIN_SAMPLES", 10) or 10)
        error_rate_threshold = float(getattr(settings, "MCP_CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD", 0.5) or 0.5)
        window_s = float(getattr(settings, "MCP_CIRCUIT_BREAKER_WINDOW_SECONDS", 60.0) or 60.0)
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
                start = float(st.get("window_start", 0.0) or 0.0)
                if start <= 0.0 or (now - start) >= window_s:
                    st["window_start"] = now
                    st["window_total"] = 0
                    st["window_failures"] = 0
                st["window_total"] = int(st.get("window_total", 0) or 0) + 1
                st["window_failures"] = int(st.get("window_failures", 0) or 0) + 1
                return
            if not retryable:
                if state == "half_open":
                    st["state"] = "closed"
                    st["half_open_probes"] = 0
                start = float(st.get("window_start", 0.0) or 0.0)
                if start <= 0.0 or (now - start) >= window_s:
                    st["window_start"] = now
                    st["window_total"] = 0
                    st["window_failures"] = 0
                st["window_total"] = int(st.get("window_total", 0) or 0) + 1
                return
            failures = int(st.get("failures", 0) or 0) + 1
            st["failures"] = failures
            start = float(st.get("window_start", 0.0) or 0.0)
            if start <= 0.0 or (now - start) >= window_s:
                st["window_start"] = now
                st["window_total"] = 0
                st["window_failures"] = 0
            st["window_total"] = int(st.get("window_total", 0) or 0) + 1
            st["window_failures"] = int(st.get("window_failures", 0) or 0) + 1
            window_total = int(st.get("window_total", 0) or 0)
            window_failures = int(st.get("window_failures", 0) or 0)
            error_rate_open = window_total >= max(1, min_samples) and (window_failures / max(1, window_total)) >= error_rate_threshold
            if failures >= max(1, threshold) or error_rate_open:
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
        tool_name: Optional[str] = None,
        tenant_tier: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        write_retry_safe: Optional[bool] = None,
    ) -> Dict[str, Any]:
        policy = self._resolve_runtime_policy(
            operation_class=operation_class,
            tenant_tier=tenant_tier,
            tool_name=tool_name,
            write_retry_safe=write_retry_safe,
        )
        operation_class = str(policy.get("operation_class") or operation_class)
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
            if policy.get("write_retry_safe") is True or (idempotency_key and str(idempotency_key).strip()):
                pass
            else:
                attempts = min(attempts, 1)
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
        if policy.get("invocation_max_attempts") is not None:
            try:
                attempts = int(policy.get("invocation_max_attempts") or attempts)
            except Exception:
                pass
        if policy.get("retry_base_delay_seconds") is not None:
            try:
                base_delay = float(policy.get("retry_base_delay_seconds") or base_delay)
            except Exception:
                pass
        if policy.get("retry_max_delay_seconds") is not None:
            try:
                max_delay = float(policy.get("retry_max_delay_seconds") or max_delay)
            except Exception:
                pass
        if policy.get("retry_jitter_seconds") is not None:
            try:
                jitter = float(policy.get("retry_jitter_seconds") or jitter)
            except Exception:
                pass
        attempts = max(1, attempts)
        bounded_timeout = self._bounded_timeout(timeout_seconds)
        last_exc: Optional[BaseException] = None

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            acquired_quota = False
            await self._circuit_preflight(target_key)
            await self._acquire_quota(business_id, target_key, policy)
            acquired_quota = True
            increment_event("admitted", code="none", operation_class=operation_class, target_key=target_key)
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
                observe_duration(
                    time.perf_counter() - started,
                    operation_class=operation_class,
                    target_key=target_key,
                    outcome="success",
                )
                increment_event("success", code="none", operation_class=operation_class, target_key=target_key)
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
                observe_duration(
                    time.perf_counter() - started,
                    operation_class=operation_class,
                    target_key=target_key,
                    outcome="guardrail_error",
                )
                increment_event("rejected", code=ge.code, operation_class=operation_class, target_key=target_key)
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
                    increment_event("retry", code=ge.code, operation_class=operation_class, target_key=target_key)
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
                observe_duration(
                    time.perf_counter() - started,
                    operation_class=operation_class,
                    target_key=target_key,
                    outcome="call_error",
                )
                increment_event("error", code=cls.code, operation_class=operation_class, target_key=target_key)
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
                    increment_event("retry", code=cls.code, operation_class=operation_class, target_key=target_key)
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


def resolve_mcp_tenant_tier(business_id: int) -> str:
    """Tenant tier for MCP guardrail policy (bronze/silver/gold). Single source for routes + executor."""
    try:
        raw = str(getattr(settings, "MCP_BUSINESS_TIER_BY_ID_JSON", "{}") or "{}")
        mapping = json.loads(raw)
        if isinstance(mapping, dict):
            v = str(mapping.get(str(int(business_id))) or "").strip().lower()
            if v in {"bronze", "silver", "gold"}:
                return v
    except Exception:
        pass
    return str(getattr(settings, "MCP_DEFAULT_BUSINESS_TIER", "bronze") or "bronze").strip().lower()


def byo_mcp_target_key(connection_id: int, base_url: str, endpoint_path: str, method: str) -> str:
    """
    Stable breaker/quota key for BYO MCP JSON-RPC (matches agent external tool_key shape).

    connection_id is a positive DB id for saved connections. For unsaved validate flows, pass a
    negative sentinel ``-(user_id + 1)`` so keys are per-tenant and never collide with real rows.
    """
    base = (base_url or "").strip().rstrip("/")
    ep = (endpoint_path or "/mcp").strip()
    if not ep.startswith("/"):
        ep = "/" + ep
    return f"external:{int(connection_id)}:{base}:{ep}:rpc:{method}"


def infer_rpc_operation_class(method: str) -> Literal["read_like", "write_like"]:
    """tools/call may mutate upstream; use write_like retry policy."""
    if (method or "").strip().lower() == "tools/call":
        return "write_like"
    return "read_like"


def infer_mcp_tool_operation_class(
    tool_name: str, arguments: Optional[Dict[str, Any]]
) -> Literal["read_like", "write_like"]:
    """
    Classify platform / agent MCP tool invocations for guardrail retry policy.

    Shared by ``AgentExecutor`` and HTTP platform tool routes so read vs write profiles stay aligned.
    """
    name = (tool_name or "").strip().lower()
    args = arguments or {}
    operation_type = str(args.get("operation_type") or "").strip().lower()
    write_mode = str(args.get("write_mode") or "").strip().lower()
    if operation_type in {"write", "create", "update", "delete", "insert", "upsert", "merge"}:
        return "write_like"
    if write_mode in {"replace", "append", "merge", "upsert"}:
        return "write_like"
    if any(
        token in name
        for token in ("write", "create", "update", "delete", "insert", "upsert", "patch", "save", "merge", "sync")
    ):
        return "write_like"
    return "read_like"


def classify_mcp_failure(exc: BaseException) -> MCPErrorClassification:
    """
    Public classifier for MCP-related failures (GitHub #116): stable code + retryable flag + detail.
    Used by guardrails internally; exposed for tests and diagnostics.
    """
    return MCPInvocationGuardrails._classify_exception(exc)


async def guarded_mcp_jsonrpc(
    *,
    business_id: int,
    connection_id: int,
    base_url: str,
    endpoint_path: str,
    method: str,
    params: Optional[Dict[str, Any]],
    auth_type: str,
    credentials: Optional[Dict[str, Any]],
    timeout_seconds: float,
    operation_class: Optional[Literal["read_like", "write_like"]] = None,
) -> Dict[str, Any]:
    """
    JSON-RPC to an MCP server under shared guardrails (timeouts, classified retries, breaker, quotas).
    Imports call_mcp_server inside execute_call so tests can patch services.mcp_client.call_mcp_server.
    """
    op_cls = operation_class if operation_class is not None else infer_rpc_operation_class(method)
    op_cls = cast(Literal["read_like", "write_like"], op_cls)
    guard = get_mcp_guardrails()
    ep = (endpoint_path or "/mcp").strip()
    if not ep.startswith("/"):
        ep = "/" + ep
    target_key = byo_mcp_target_key(connection_id, base_url, ep, method)
    tenant_tier = resolve_mcp_tenant_tier(int(business_id))

    async def _exec(bounded_timeout: float):
        from services.mcp_client import call_mcp_server

        return await call_mcp_server(
            base_url=base_url.strip().rstrip("/"),
            endpoint_path=ep,
            method=method,
            params=params,
            auth_type=auth_type,
            credentials=credentials,
            timeout=bounded_timeout,
        )

    return await guard.call_tool_with_guardrails(
        business_id=int(business_id),
        target_key=target_key,
        timeout_seconds=timeout_seconds,
        operation_class=op_cls,
        tool_name=f"rpc:{method}",
        tenant_tier=tenant_tier,
        idempotency_key="",
        execute_call=_exec,
    )


async def guarded_mcp_list_tools(
    *,
    business_id: int,
    connection_id: int,
    base_url: str,
    endpoint_path: str,
    auth_type: str,
    credentials: Optional[Dict[str, Any]],
    timeout_seconds: float,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    MCP tools/list under guardrails. Lazy-imports list_tools for test patches.
    """
    guard = get_mcp_guardrails()
    ep = (endpoint_path or "/mcp").strip()
    if not ep.startswith("/"):
        ep = "/" + ep
    target_key = byo_mcp_target_key(connection_id, base_url, ep, "tools/list")
    tenant_tier = resolve_mcp_tenant_tier(int(business_id))

    async def _exec(bounded_timeout: float):
        from services.mcp_client import list_tools as mcp_list_tools

        return await mcp_list_tools(
            base_url=base_url.strip().rstrip("/"),
            endpoint_path=ep,
            auth_type=auth_type,
            credentials=credentials,
            timeout=bounded_timeout,
            extra_headers=extra_headers,
        )

    return await guard.call_tool_with_guardrails(
        business_id=int(business_id),
        target_key=target_key,
        timeout_seconds=timeout_seconds,
        operation_class="read_like",
        tool_name="rpc:tools/list",
        tenant_tier=tenant_tier,
        idempotency_key="",
        execute_call=_exec,
    )
