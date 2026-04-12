"""Validate-tool HTTP path applies SSRF checks (rest_api / elasticsearch)."""

import pytest

from services.mcp_validate import validate_tool_config


def test_validate_rest_api_rejects_loopback(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_ALLOW_PRIVATE_URLS", raising=False)
    ok, msg = validate_tool_config("rest_api", {"base_url": "http://127.0.0.1:3000"})
    assert ok is False
    assert "private" in msg.lower() or "loopback" in msg.lower()


def test_validate_elasticsearch_rejects_loopback(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_ALLOW_PRIVATE_URLS", raising=False)
    ok, msg = validate_tool_config("elasticsearch", {"url": "http://localhost:9200"})
    assert ok is False
