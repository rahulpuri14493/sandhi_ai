import asyncio

import pytest
import httpx

from services.mcp_guardrails import (
    MCPGuardrailError,
    MCPInvocationGuardrails,
    classify_mcp_failure,
    infer_mcp_tool_operation_class,
)
from services.mcp_client import MCPJSONRPCError
from services import mcp_metrics


@pytest.fixture(autouse=True)
def _guardrails_retry_profile_defaults(monkeypatch):
    # Keep legacy retry knobs authoritative unless a test explicitly overrides read/write profiles.
    monkeypatch.setattr("core.config.settings.MCP_READ_INVOCATION_MAX_ATTEMPTS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_READ_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_READ_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_READ_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_MAX_ATTEMPTS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)


@pytest.mark.asyncio
async def test_guardrails_retry_transient_then_success(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    calls = {"n": 0}

    async def _do_call(_timeout: float):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("read timeout")
        return {"content": [{"type": "text", "text": "ok"}]}

    out = await g.call_tool_with_guardrails(
        business_id=101,
        target_key="platform:test:/mcp:query",
        timeout_seconds=5.0,
        execute_call=_do_call,
    )
    assert calls["n"] == 3
    assert out["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_guardrails_no_retry_non_retryable_error(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    calls = {"n": 0}

    async def _do_call(_timeout: float):
        calls["n"] += 1
        raise RuntimeError("invalid argument payload")

    with pytest.raises(MCPGuardrailError) as exc:
        await g.call_tool_with_guardrails(
            business_id=102,
            target_key="external:5:https://mcp:/mcp:write",
            timeout_seconds=10.0,
            execute_call=_do_call,
        )
    assert calls["n"] == 1
    assert exc.value.code in {"mcp_tool_validation_failed", "mcp_unknown"}


@pytest.mark.asyncio
async def test_guardrails_circuit_open_after_threshold(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 2, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 60.0, raising=False)
    g = MCPInvocationGuardrails()

    async def _fail(_timeout: float):
        raise httpx.ConnectError("down")

    with pytest.raises(MCPGuardrailError):
        await g.call_tool_with_guardrails(
            business_id=103,
            target_key="platform:test:/mcp:insert",
            timeout_seconds=3.0,
            execute_call=_fail,
        )
    with pytest.raises(MCPGuardrailError):
        await g.call_tool_with_guardrails(
            business_id=103,
            target_key="platform:test:/mcp:insert",
            timeout_seconds=3.0,
            execute_call=_fail,
        )

    with pytest.raises(MCPGuardrailError) as exc:
        await g.call_tool_with_guardrails(
            business_id=103,
            target_key="platform:test:/mcp:insert",
            timeout_seconds=3.0,
            execute_call=_fail,
        )
    assert exc.value.code == "mcp_circuit_open"


@pytest.mark.asyncio
async def test_guardrails_tenant_concurrency_quota(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    gate = asyncio.Event()

    async def _slow(_timeout: float):
        await gate.wait()
        return {"content": [{"type": "text", "text": "ok"}]}

    first = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=104,
            target_key="platform:test:/mcp:list",
            timeout_seconds=4.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.02)

    with pytest.raises(MCPGuardrailError) as exc:
        await g.call_tool_with_guardrails(
            business_id=104,
            target_key="platform:test:/mcp:list",
            timeout_seconds=4.0,
            execute_call=_slow,
        )
    assert exc.value.code == "mcp_quota_exceeded"
    gate.set()
    out = await first
    assert out["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_guardrails_target_concurrency_quota(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    gate = asyncio.Event()

    async def _slow(_timeout: float):
        await gate.wait()
        return {"content": [{"type": "text", "text": "ok"}]}

    first = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=105,
            target_key="external:42:https://mcp.example.com:/mcp:write_row",
            timeout_seconds=4.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.02)
    with pytest.raises(MCPGuardrailError) as exc:
        await g.call_tool_with_guardrails(
            business_id=106,
            target_key="external:42:https://mcp.example.com:/mcp:write_row",
            timeout_seconds=4.0,
            execute_call=_slow,
        )
    assert exc.value.code == "mcp_quota_exceeded"
    gate.set()
    await first


@pytest.mark.asyncio
async def test_guardrails_tenant_rate_limit_per_minute(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_RATE_LIMIT_PER_MINUTE", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 0, raising=False)
    g = MCPInvocationGuardrails()

    async def _ok(_timeout: float):
        return {"content": [{"type": "text", "text": "ok"}]}

    await g.call_tool_with_guardrails(
        business_id=107,
        target_key="platform:test:/mcp:query",
        timeout_seconds=4.0,
        execute_call=_ok,
    )
    with pytest.raises(MCPGuardrailError) as exc:
        await g.call_tool_with_guardrails(
            business_id=107,
            target_key="platform:test:/mcp:query",
            timeout_seconds=4.0,
            execute_call=_ok,
        )
    assert exc.value.code == "mcp_rate_limited"


@pytest.mark.asyncio
async def test_guardrails_circuit_half_open_success_closes(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_OPEN_SECONDS", 0.01, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_HALF_OPEN_MAX_PROBES", 1, raising=False)
    g = MCPInvocationGuardrails()

    async def _fail(_timeout: float):
        raise httpx.ConnectError("down")

    async def _ok(_timeout: float):
        return {"content": [{"type": "text", "text": "ok"}]}

    with pytest.raises(MCPGuardrailError):
        await g.call_tool_with_guardrails(
            business_id=108,
            target_key="platform:test:/mcp:insert",
            timeout_seconds=2.0,
            execute_call=_fail,
        )
    await asyncio.sleep(0.02)
    out = await g.call_tool_with_guardrails(
        business_id=108,
        target_key="platform:test:/mcp:insert",
        timeout_seconds=2.0,
        execute_call=_ok,
    )
    assert out["content"][0]["text"] == "ok"
    # Closed again: immediate follow-up succeeds.
    out2 = await g.call_tool_with_guardrails(
        business_id=108,
        target_key="platform:test:/mcp:insert",
        timeout_seconds=2.0,
        execute_call=_ok,
    )
    assert out2["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_guardrails_http_status_classification(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    g = MCPInvocationGuardrails()

    req = httpx.Request("POST", "https://mcp.example.com/mcp")
    resp_503 = httpx.Response(status_code=503, request=req)
    resp_400 = httpx.Response(status_code=400, request=req)

    async def _503(_timeout: float):
        raise httpx.HTTPStatusError("upstream down", request=req, response=resp_503)

    async def _400(_timeout: float):
        raise httpx.HTTPStatusError("bad request", request=req, response=resp_400)

    with pytest.raises(MCPGuardrailError) as e503:
        await g.call_tool_with_guardrails(
            business_id=109,
            target_key="platform:test:/mcp:query",
            timeout_seconds=3.0,
            execute_call=_503,
        )
    assert e503.value.code == "mcp_upstream_unavailable"
    assert e503.value.retryable is True

    with pytest.raises(MCPGuardrailError) as e400:
        await g.call_tool_with_guardrails(
            business_id=109,
            target_key="platform:test:/mcp:query",
            timeout_seconds=3.0,
            execute_call=_400,
        )
    assert e400.value.code == "mcp_tool_validation_failed"
    assert e400.value.retryable is False


@pytest.mark.asyncio
async def test_guardrails_timeout_is_bounded_by_max(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TOOL_MAX_TIMEOUT_SECONDS", 7.0, raising=False)
    g = MCPInvocationGuardrails()
    seen = {"timeout": None}

    async def _capture(timeout: float):
        seen["timeout"] = timeout
        return {"content": [{"type": "text", "text": "ok"}]}

    await g.call_tool_with_guardrails(
        business_id=110,
        target_key="platform:test:/mcp:list",
        timeout_seconds=999.0,
        execute_call=_capture,
    )
    assert seen["timeout"] == 7.0


@pytest.mark.asyncio
async def test_guardrails_process_local_fairness_isolation(monkeypatch):
    """
    Guardrail state is process-local by design:
    two independent instances do not share quotas/breaker state (multi-worker caveat).
    """
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.0, raising=False)
    g1 = MCPInvocationGuardrails()
    g2 = MCPInvocationGuardrails()
    gate = asyncio.Event()

    async def _slow(_timeout: float):
        await gate.wait()
        return {"content": [{"type": "text", "text": "ok"}]}

    t1 = asyncio.create_task(
        g1.call_tool_with_guardrails(
            business_id=111,
            target_key="platform:test:/mcp:query",
            timeout_seconds=5.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.02)
    # Same business_id on a different guardrail instance is admitted (state is not shared).
    t2 = asyncio.create_task(
        g2.call_tool_with_guardrails(
            business_id=111,
            target_key="platform:test:/mcp:query",
            timeout_seconds=5.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.02)
    assert not t2.done()
    gate.set()
    out1 = await t1
    out2 = await t2
    assert out1["content"][0]["text"] == "ok"
    assert out2["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_guardrails_allows_two_parallel_same_tool_when_target_limit_two(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 10, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 2, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.5, raising=False)
    g = MCPInvocationGuardrails()
    gate = asyncio.Event()

    async def _slow(_timeout: float):
        await gate.wait()
        return {"content": [{"type": "text", "text": "ok"}]}

    t1 = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=112,
            target_key="platform:test:/mcp:same_tool",
            timeout_seconds=3.0,
            execute_call=_slow,
        )
    )
    t2 = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=112,
            target_key="platform:test:/mcp:same_tool",
            timeout_seconds=3.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.03)
    assert not t1.done()
    assert not t2.done()
    gate.set()
    out1 = await t1
    out2 = await t2
    assert out1["content"][0]["text"] == "ok"
    assert out2["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_quota_saturation_does_not_open_circuit(monkeypatch):
    """
    Local concurrency pressure should not be treated as upstream failure.
    After a quota rejection, a later admitted call must still proceed normally.
    """
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 1, raising=False)
    g = MCPInvocationGuardrails()
    gate = asyncio.Event()

    async def _slow(_timeout: float):
        await gate.wait()
        return {"content": [{"type": "text", "text": "ok"}]}

    first = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=113,
            target_key="platform:test:/mcp:same",
            timeout_seconds=3.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.02)
    with pytest.raises(MCPGuardrailError) as quota_err:
        await g.call_tool_with_guardrails(
            business_id=113,
            target_key="platform:test:/mcp:same",
            timeout_seconds=3.0,
            execute_call=_slow,
        )
    assert quota_err.value.code == "mcp_quota_exceeded"
    gate.set()
    await first

    # If quota errors incorrectly counted as breaker failures, this would be mcp_circuit_open.
    async def _ok(_timeout: float):
        return {"content": [{"type": "text", "text": "ok"}]}

    out = await g.call_tool_with_guardrails(
        business_id=113,
        target_key="platform:test:/mcp:same",
        timeout_seconds=3.0,
        execute_call=_ok,
    )
    assert out["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_same_target_two_agents_one_waits_then_succeeds(monkeypatch):
    """
    With target concurrency=1 and positive wait budget, second concurrent request should queue
    briefly and succeed once the first one releases.
    """
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 10, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.5, raising=False)
    g = MCPInvocationGuardrails()
    gate = asyncio.Event()

    async def _slow(_timeout: float):
        await gate.wait()
        return {"content": [{"type": "text", "text": "ok"}]}

    t1 = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=114,
            target_key="platform:test:/mcp:hot_tool",
            timeout_seconds=3.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.02)
    t2 = asyncio.create_task(
        g.call_tool_with_guardrails(
            business_id=114,
            target_key="platform:test:/mcp:hot_tool",
            timeout_seconds=3.0,
            execute_call=_slow,
        )
    )
    await asyncio.sleep(0.05)
    # second should be queued, not failed yet
    assert not t2.done()
    gate.set()
    out1 = await t1
    out2 = await t2
    assert out1["content"][0]["text"] == "ok"
    assert out2["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_guardrails_classifies_jsonrpc_structured_errors(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    g = MCPInvocationGuardrails()

    async def _invalid(_timeout: float):
        raise MCPJSONRPCError("invalid params", rpc_code=-32602, rpc_data={"status": 400})

    async def _throttled(_timeout: float):
        raise MCPJSONRPCError("busy", rpc_code=-32002, rpc_data={"status": 429})

    with pytest.raises(MCPGuardrailError) as invalid_exc:
        await g.call_tool_with_guardrails(
            business_id=115,
            target_key="platform:test:/mcp:tool",
            timeout_seconds=2.0,
            execute_call=_invalid,
        )
    assert invalid_exc.value.code == "mcp_tool_validation_failed"
    assert invalid_exc.value.retryable is False

    with pytest.raises(MCPGuardrailError) as throttled_exc:
        await g.call_tool_with_guardrails(
            business_id=115,
            target_key="platform:test:/mcp:tool",
            timeout_seconds=2.0,
            execute_call=_throttled,
        )
    assert throttled_exc.value.code == "mcp_rate_limited"
    assert throttled_exc.value.retryable is True


@pytest.mark.asyncio
async def test_guardrails_write_retry_profile_override(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_MAX_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    calls = {"n": 0}

    async def _always_retryable(_timeout: float):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    with pytest.raises(MCPGuardrailError):
        await g.call_tool_with_guardrails(
            business_id=116,
            target_key="platform:test:/mcp:write_tool",
            timeout_seconds=2.0,
            operation_class="write_like",
            idempotency_key="safe-1",
            execute_call=_always_retryable,
        )
    assert calls["n"] == 2


def test_redis_rate_key_uses_epoch_minute_bucket():
    g = MCPInvocationGuardrails()
    k = g._redis_rate_key(42, 125.0)
    assert k.endswith(":rate:tenant:42:2")


@pytest.mark.asyncio
async def test_distributed_quota_rate_limit_code_from_atomic_eval(monkeypatch):
    class _FakeRedis:
        def eval(self, *_args, **_kwargs):
            return -1  # rate limited

    monkeypatch.setattr("core.config.settings.MCP_TENANT_RATE_LIMIT_PER_MINUTE", 10, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    g._redis_client = _FakeRedis()
    g._redis_init_failed = False
    monkeypatch.setattr(g, "_get_redis_client", lambda: g._redis_client)
    with pytest.raises(MCPGuardrailError) as exc:
        await g._acquire_quota_distributed(77, "platform:test:/mcp:x", {})
    assert exc.value.code == "mcp_rate_limited"


@pytest.mark.asyncio
async def test_distributed_circuit_preflight_blocks_when_atomic_eval_rejects(monkeypatch):
    class _FakeRedis:
        def eval(self, *_args, **_kwargs):
            return 0  # open

    g = MCPInvocationGuardrails()
    g._redis_client = _FakeRedis()
    g._redis_init_failed = False
    monkeypatch.setattr(g, "_get_redis_client", lambda: g._redis_client)
    with pytest.raises(MCPGuardrailError) as exc:
        await g._circuit_preflight("platform:test:/mcp:y")
    assert exc.value.code == "mcp_circuit_open"


@pytest.mark.asyncio
async def test_write_like_without_idempotency_forces_single_attempt(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_MAX_ATTEMPTS", 4, raising=False)
    g = MCPInvocationGuardrails()
    calls = {"n": 0}

    async def _retryable(_timeout: float):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    with pytest.raises(MCPGuardrailError):
        await g.call_tool_with_guardrails(
            business_id=117,
            target_key="platform:test:/mcp:write_no_idem",
            timeout_seconds=2.0,
            operation_class="write_like",
            execute_call=_retryable,
        )
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_write_like_with_idempotency_allows_retries(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_MAX_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_BASE_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_MAX_DELAY_SECONDS", 0.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_WRITE_INVOCATION_RETRY_JITTER_SECONDS", 0.0, raising=False)
    g = MCPInvocationGuardrails()
    calls = {"n": 0}

    async def _retryable(_timeout: float):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    with pytest.raises(MCPGuardrailError):
        await g.call_tool_with_guardrails(
            business_id=118,
            target_key="platform:test:/mcp:write_with_idem",
            timeout_seconds=2.0,
            operation_class="write_like",
            idempotency_key="job-1-step-2",
            execute_call=_retryable,
        )
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_tier_and_tool_policy_override(monkeypatch):
    monkeypatch.setattr(
        "core.config.settings.MCP_TENANT_TIER_POLICY_JSON",
        '{"silver":{"tenant_max_concurrent_calls":2}}',
        raising=False,
    )
    monkeypatch.setattr(
        "core.config.settings.MCP_TOOL_POLICY_JSON",
        '{"search_tool":{"operation_class":"read_like","invocation_max_attempts":2}}',
        raising=False,
    )
    g = MCPInvocationGuardrails()
    p = g._resolve_runtime_policy(operation_class="write_like", tenant_tier="silver", tool_name="search_tool", write_retry_safe=None)
    assert p["tenant_max_concurrent_calls"] == 2
    assert p["invocation_max_attempts"] == 2
    assert p["operation_class"] == "read_like"


@pytest.mark.asyncio
async def test_circuit_rolling_window_error_rate_opens(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 999, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_WINDOW_SECONDS", 60.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_MIN_SAMPLES", 2, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD", 0.5, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    g = MCPInvocationGuardrails()

    target = "platform:test:/mcp:rolling"
    await g._circuit_record_failure(target, retryable=True)
    await g._circuit_record_success(target)
    await g._circuit_record_failure(target, retryable=True)
    with pytest.raises(MCPGuardrailError) as exc_open:
        await g._circuit_preflight(target)
    assert exc_open.value.code == "mcp_circuit_open"


def test_metrics_target_key_hash_mode(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MODE", "hash", raising=False)
    out = mcp_metrics._normalize_target_key("platform:https://host:/mcp:very_long_tool_name")
    assert out.startswith("h:")
    assert len(out) <= 18


def test_metrics_target_key_normalized_mode(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MODE", "normalized", raising=False)
    out = mcp_metrics._normalize_target_key("external:99:https://foo:/mcp:write_row")
    assert out.startswith("external:")
    assert out.endswith(":write_row")


def test_redis_recovery_window_allows_reenable(monkeypatch):
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_DISTRIBUTED_ENABLED", True, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_REDIS_RETRY_SECONDS", 1.0, raising=False)
    g = MCPInvocationGuardrails()
    dummy = object()
    g._redis_client = dummy
    g._redis_init_failed = True
    g._redis_retry_after_monotonic = 0.0  # expired; should retry path and then reuse client
    out = g._get_redis_client()
    assert out is dummy
    assert g._redis_init_failed is False


def test_infer_mcp_tool_operation_class_matches_platform_and_byo_naming():
    """Shared helper must stay aligned with HTTP platform routes and AgentExecutor."""
    assert infer_mcp_tool_operation_class("platform_9_snowflake", {"query": "SELECT 1"}) == "read_like"
    assert infer_mcp_tool_operation_class("platform_9_snowflake", {"operation_type": "insert"}) == "write_like"
    assert infer_mcp_tool_operation_class("byo_1_write_record", {}) == "write_like"


def test_classify_mcp_failure_public_alias_matches_transport_errors():
    """#116: stable classifier API for policy and diagnostics."""
    c = classify_mcp_failure(httpx.TransportError("connection failed"))
    assert c.retryable is True
    assert c.code == "mcp_upstream_unavailable"


def test_http_exception_maps_upstream_unavailable_to_503():
    """#116: MCP HTTP routes treat classified upstream failure as 503 (retryable dependency down)."""
    from api.routes.mcp import _http_exception_from_mcp_guardrail_error

    ge = MCPGuardrailError("mcp_upstream_unavailable", "MCP transport failure", retryable=True)
    http_exc = _http_exception_from_mcp_guardrail_error(ge)
    assert http_exc.status_code == 503
    assert http_exc.detail["error"] == "mcp_upstream_unavailable"
