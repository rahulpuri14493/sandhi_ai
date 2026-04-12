"""
Black-box vector E2E against a **running** Sandhi deployment (same DB as your manual tests).

Set:
  VECTOR_E2E_API_BASE_URL   e.g. https://api.example.com or http://localhost:8000
  VECTOR_E2E_ACCESS_TOKEN    JWT after login
  VECTOR_E2E_PLATFORM_TOOL_NAME  e.g. platform_12_MyWeaviate

Or multiple calls:
  VECTOR_E2E_TOOL_CALLS_JSON='[{"tool_name":"platform_12_W","query":"hi","top_k":3}]'

Optional: VECTOR_E2E_QUERY, VECTOR_E2E_TOP_K, VECTOR_E2E_TIMEOUT_SECONDS

Run: pytest tests/integration/test_vector_e2e_deployed.py -m vector_e2e -v
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

pytestmark = pytest.mark.vector_e2e


def _auth_base() -> tuple[str, str] | None:
    base = (os.environ.get("VECTOR_E2E_API_BASE_URL") or "").strip().rstrip("/")
    token = (os.environ.get("VECTOR_E2E_ACCESS_TOKEN") or "").strip()
    if not base or not token:
        return None
    return base, token


def _cases() -> list[dict]:
    raw = (os.environ.get("VECTOR_E2E_TOOL_CALLS_JSON") or "").strip()
    if raw:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("VECTOR_E2E_TOOL_CALLS_JSON must be a JSON array")
        return data
    name = (os.environ.get("VECTOR_E2E_PLATFORM_TOOL_NAME") or "").strip()
    if not name:
        return []
    q = (os.environ.get("VECTOR_E2E_QUERY") or "test").strip()
    top_k = int((os.environ.get("VECTOR_E2E_TOP_K") or "3").strip() or "3")
    return [{"tool_name": name, "query": q, "top_k": top_k}]


def test_deployed_call_platform_vector_tools():
    auth = _auth_base()
    cases = _cases()
    if not auth or not cases:
        pytest.skip(
            "Set VECTOR_E2E_API_BASE_URL + VECTOR_E2E_ACCESS_TOKEN and "
            "VECTOR_E2E_PLATFORM_TOOL_NAME or VECTOR_E2E_TOOL_CALLS_JSON"
        )
    base, token = auth
    timeout = float((os.environ.get("VECTOR_E2E_TIMEOUT_SECONDS") or "120").strip() or "120")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for i, case in enumerate(cases):
        tool_name = (case.get("tool_name") or "").strip()
        assert tool_name, f"case[{i}] missing tool_name"
        body: dict = {
            "tool_name": tool_name,
            "arguments": {
                "query": (case.get("query") or "test").strip(),
                "top_k": min(max(int(case.get("top_k") or 3), 1), 100),
            },
        }
        if case.get("timeout_seconds") is not None:
            body["timeout_seconds"] = float(case["timeout_seconds"])
        r = httpx.post(
            f"{base}/api/mcp/call-platform-tool",
            headers=headers,
            json=body,
            timeout=timeout,
        )
        assert r.status_code == 200, f"{tool_name}: HTTP {r.status_code} {r.text[:800]}"
        data = r.json()
        assert not data.get("isError"), data
        parts = [
            b.get("text") or ""
            for b in (data.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text = "\n".join(parts)
        assert text.strip(), f"{tool_name}: empty result"
        assert not text.lstrip().startswith("Error:"), f"{tool_name}: {text[:1500]}"
