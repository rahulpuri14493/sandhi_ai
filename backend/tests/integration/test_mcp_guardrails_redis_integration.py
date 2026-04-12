import asyncio
import os

import pytest

from services.mcp_guardrails import MCPInvocationGuardrails


def _redis_url() -> str:
    return (
        os.getenv("MCP_GUARDRAILS_REDIS_URL")
        or os.getenv("REDIS_URL")
        or ""
    ).strip()


def _purge_prefix(redis_client, prefix: str) -> None:
    try:
        keys = list(redis_client.scan_iter(match=f"{prefix}*"))
        if keys:
            redis_client.delete(*keys)
    except Exception:
        pass


@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_distributed_parallel_same_target_high_concurrency(monkeypatch):
    url = _redis_url()
    if not url:
        pytest.skip("Redis URL not configured for redis_integration tests")

    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_DISTRIBUTED_ENABLED", True, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_REDIS_URL", url, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_REDIS_PREFIX", "sandhi:test:mcp_guardrails:race", raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 500, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 30, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 10.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_READ_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_FAIR_QUEUE_ENABLED", True, raising=False)

    g = MCPInvocationGuardrails()
    r = g._get_redis_client()
    if r is None:
        pytest.skip("Redis client unavailable")
    _purge_prefix(r, "sandhi:test:mcp_guardrails:race")

    async def _call(_timeout: float):
        await asyncio.sleep(0.03)
        return {"content": [{"type": "text", "text": "ok"}]}

    tasks = [
        asyncio.create_task(
            g.call_tool_with_guardrails(
                business_id=901,
                target_key="platform:test:/mcp:hot_tool",
                timeout_seconds=3.0,
                operation_class="read_like",
                tool_name="hot_tool",
                tenant_tier="gold",
                execute_call=_call,
            )
        )
        for _ in range(120)
    ]
    out = await asyncio.gather(*tasks)
    assert len(out) == 120
    assert all(item["content"][0]["text"] == "ok" for item in out)


@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_distributed_fair_queue_fifo_when_target_serial(monkeypatch):
    url = _redis_url()
    if not url:
        pytest.skip("Redis URL not configured for redis_integration tests")

    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_DISTRIBUTED_ENABLED", True, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_REDIS_URL", url, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_REDIS_PREFIX", "sandhi:test:mcp_guardrails:fifo", raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TENANT_MAX_CONCURRENT_CALLS", 20, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_TARGET_MAX_CONCURRENT_CALLS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_CONCURRENCY_WAIT_SECONDS", 5.0, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_READ_INVOCATION_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_FAIR_QUEUE_ENABLED", True, raising=False)
    monkeypatch.setattr("core.config.settings.MCP_GUARDRAILS_FAIR_QUEUE_STALE_SECONDS", 20.0, raising=False)

    g = MCPInvocationGuardrails()
    r = g._get_redis_client()
    if r is None:
        pytest.skip("Redis client unavailable")
    _purge_prefix(r, "sandhi:test:mcp_guardrails:fifo")

    completion_order: list[int] = []

    async def _call(_timeout: float):
        await asyncio.sleep(0.02)
        return {"content": [{"type": "text", "text": "ok"}]}

    async def _runner(i: int):
        result = await g.call_tool_with_guardrails(
            business_id=902,
            target_key="platform:test:/mcp:serial_tool",
            timeout_seconds=2.0,
            operation_class="read_like",
            tool_name="serial_tool",
            tenant_tier="silver",
            execute_call=_call,
        )
        completion_order.append(i)
        return result

    tasks = []
    for i in range(20):
        tasks.append(asyncio.create_task(_runner(i)))
        await asyncio.sleep(0.001)
    results = await asyncio.gather(*tasks)
    assert all(r["content"][0]["text"] == "ok" for r in results)
    assert completion_order[:10] == list(range(10))
