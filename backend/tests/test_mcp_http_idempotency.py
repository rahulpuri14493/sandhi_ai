"""Unit tests for MCP HTTP idempotency cache helpers."""
from unittest.mock import MagicMock

from services import mcp_http_idempotency as mid


def test_http_idempotency_cache_key_stable_per_inputs():
    k1 = mid.http_idempotency_cache_key(1, "platform:http://h:/mcp:t", "my-key")
    k2 = mid.http_idempotency_cache_key(1, "platform:http://h:/mcp:t", "my-key")
    assert k1 == k2
    assert k1 != mid.http_idempotency_cache_key(2, "platform:http://h:/mcp:t", "my-key")
    assert k1 != mid.http_idempotency_cache_key(1, "platform:http://h:/mcp:t", "other")


def test_should_cache_mcp_tool_result():
    assert mid.should_cache_mcp_tool_result({"content": [{"type": "text", "text": "{}"}]}) is True
    assert mid.should_cache_mcp_tool_result({"isError": True, "content": []}) is False
    assert mid.should_cache_mcp_tool_result("not a dict") is False


def test_try_get_and_store_roundtrip(monkeypatch):
    fake = MagicMock()
    fake.get.return_value = None
    monkeypatch.setattr(mid, "get_http_idempotency_redis", lambda: fake)
    assert mid.try_get_cached_tool_result(1, "t", "k") is None
    payload = {"content": [{"type": "text", "text": "ok"}]}
    fake.get.return_value = '{"content":[{"type":"text","text":"ok"}]}'
    assert mid.try_get_cached_tool_result(1, "t", "k") == payload
    mid.store_cached_tool_result(1, "t", "k", payload)
    assert fake.setex.called
