import asyncio

import pytest
import httpx

from services.mcp_guardrails import MCPGuardrailError, MCPInvocationGuardrails
from services.mcp_client import MCPJSONRPCError


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
    with pytest.raises(MCPGuardrailError) as exc_open:
        await g.call_tool_with_guardrails(
            business_id=108,
            target_key="platform:test:/mcp:insert",
            timeout_seconds=2.0,
            execute_call=_ok,
        )
    assert exc_open.value.code == "mcp_circuit_open"
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
            execute_call=_always_retryable,
        )
    assert calls["n"] == 2
