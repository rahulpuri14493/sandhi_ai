"""Integration tests: JSON-RPC over HTTP with backend fetch mocked."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _rpc(
    client: TestClient,
    headers: dict,
    *,
    method: str,
    params: dict | None = None,
    req_id=1,
):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


class TestJsonRpcHeaders:
    def test_missing_business_id_returns_400(self, mcp_client: TestClient):
        r = mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 400


class TestJsonRpcInitialize:
    def test_initialize_returns_protocol(self, mcp_client: TestClient, mcp_headers: dict):
        r = _rpc(mcp_client, mcp_headers, method="initialize")
        assert r.status_code == 200
        data = r.json()
        assert data.get("jsonrpc") == "2.0"
        assert data["result"]["serverInfo"]["name"] == "sandhi-platform-mcp"


class TestJsonRpcToolsList:
    def test_tools_list_uses_backend(self, mcp_client: TestClient, mcp_headers: dict):
        fake_tools = [
            {
                "name": "platform_1_pg",
                "description": "t",
                "inputSchema": {"type": "object", "properties": {}, "required": ["query"]},
            }
        ]
        with patch("app._fetch_platform_tools", return_value=fake_tools):
            r = _rpc(mcp_client, mcp_headers, method="tools/list", req_id=2)
        assert r.status_code == 200
        data = r.json()
        assert data["result"]["tools"] == fake_tools
        assert data["result"].get("nextCursor") is None


class TestJsonRpcToolsCall:
    def test_tools_call_filesystem_read(
        self, mcp_client: TestClient, mcp_headers: dict, tmp_path
    ):
        (tmp_path / "note.txt").write_text("integration-ok", encoding="utf-8")
        payload = {
            "name": "platform_100_filesystem",
            "arguments": {"path": "note.txt", "action": "read"},
        }
        with patch(
            "app._fetch_tool_config",
            return_value={
                "config": {"base_path": str(tmp_path)},
                "tool_type": "filesystem",
                "name": "FS",
            },
        ):
            r = _rpc(mcp_client, mcp_headers, method="tools/call", params=payload, req_id=3)
        assert r.status_code == 200
        data = r.json()
        assert "error" not in data
        text = data["result"]["content"][0]["text"]
        assert text == "integration-ok"
        assert data["result"].get("isError") is False

    def test_tools_call_unknown_tool_name_returns_error(self, mcp_client: TestClient, mcp_headers: dict):
        r = _rpc(
            mcp_client,
            mcp_headers,
            method="tools/call",
            params={"name": "not_a_platform_tool", "arguments": {}},
        )
        assert r.status_code == 200
        data = r.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    def test_tools_call_backend_config_failure(
        self, mcp_client: TestClient, mcp_headers: dict
    ):
        import httpx

        def _raise(*_a, **_kw):
            req = httpx.Request("POST", "http://backend/api/internal/mcp/tools/1/config")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("backend error", request=req, response=resp)

        with patch("app._fetch_tool_config", side_effect=_raise):
            r = _rpc(
                mcp_client,
                mcp_headers,
                method="tools/call",
                params={"name": "platform_1_x", "arguments": {"query": "SELECT 1"}},
            )
        assert r.status_code == 200
        data = r.json()
        assert data.get("error", {}).get("code") == -32000
