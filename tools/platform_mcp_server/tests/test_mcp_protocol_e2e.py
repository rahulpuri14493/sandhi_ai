"""E2E-style tests: full MCP JSON-RPC sequence in one process (no mock backend for tool execution)."""
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.e2e


class TestMcpProtocolSequence:
    """Initialize → tools/list → tools/call with a real filesystem tool."""

    def test_full_sequence(self, mcp_client, mcp_headers, tmp_path, caplog):
        import logging

        caplog.set_level(logging.INFO)
        (tmp_path / "e2e.txt").write_text("e2e-data", encoding="utf-8")
        fake_list = [
            {
                "name": "platform_200_fs",
                "description": "e2e",
                "inputSchema": {"type": "object", "properties": {}, "required": ["query"]},
            }
        ]

        r0 = mcp_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "a", "method": "initialize"},
            headers=mcp_headers,
        )
        assert r0.status_code == 200
        assert r0.json()["result"]["protocolVersion"] == "2024-11-05"

        with patch("app._fetch_platform_tools", return_value=fake_list):
            r1 = mcp_client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": "b", "method": "tools/list"},
                headers=mcp_headers,
            )
        assert r1.status_code == 200
        assert len(r1.json()["result"]["tools"]) == 1

        with patch(
            "app._fetch_tool_config",
            return_value={
                "config": {"base_path": str(tmp_path)},
                "tool_type": "filesystem",
                "name": "E2E FS",
            },
        ):
            r2 = mcp_client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "c",
                    "method": "tools/call",
                    "params": {
                        "name": "platform_200_fs",
                        "arguments": {"path": "e2e.txt", "action": "read"},
                    },
                },
                headers=mcp_headers,
            )
        assert r2.status_code == 200
        body = r2.json()
        assert body["result"]["content"][0]["text"] == "e2e-data"
        assert body["result"]["isError"] is False

        # tools/call logging includes result summary for debugging
        assert any(
            "result_output=" in r.message and "e2e-data" in r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        )


class TestHealthEndpoint:
    def test_health_ok(self, mcp_client):
        r = mcp_client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "service": "platform-mcp-server"}
