"""
Optional live E2E: hit a running platform-mcp-server (e.g. Docker) over HTTP.

Set environment variable:
  PLATFORM_MCP_E2E_BASE_URL=http://localhost:PORT

Skip when unset (default CI / local unit runs).
"""
import os

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.live]


def _base_url() -> str | None:
    raw = (os.environ.get("PLATFORM_MCP_E2E_BASE_URL") or "").strip().rstrip("/")
    return raw or None


@pytest.mark.skipif(not _base_url(), reason="set PLATFORM_MCP_E2E_BASE_URL to run live MCP E2E")
def test_live_health():
    import httpx

    r = httpx.get(f"{_base_url()}/health", timeout=10.0)
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"
    assert "platform-mcp" in data.get("service", "")


@pytest.mark.skipif(not _base_url(), reason="set PLATFORM_MCP_E2E_BASE_URL to run live MCP E2E")
def test_live_initialize_jsonrpc():
    """Requires a valid business id the backend recognizes if tools/list is used later."""
    import httpx

    base = _base_url()
    bid = (os.environ.get("PLATFORM_MCP_E2E_BUSINESS_ID") or "1").strip()
    r = httpx.post(
        f"{base}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"X-MCP-Business-Id": bid},
        timeout=15.0,
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("jsonrpc") == "2.0"
    assert data.get("result", {}).get("protocolVersion")
