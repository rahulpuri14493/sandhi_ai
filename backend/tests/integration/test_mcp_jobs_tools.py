"""
Integration tests for MCP + Job tool scoping.

Focus:
- MCP connections/tools CRUD (no real network; validate calls mocked)
- Jobs create/update with allowed_platform_tool_ids/allowed_connection_ids + tool_visibility
"""

import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _auth_headers(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


class TestMCPAndJobToolScoping:
    def test_mcp_connection_crud_and_validation(self, integration_client: TestClient, business_user):
        headers = _auth_headers(business_user)

        # Validate connection (mock call_mcp_server)
        with patch("services.mcp_client.call_mcp_server", new_callable=AsyncMock) as mock:
            mock.return_value = {"jsonrpc": "2.0", "result": {"protocolVersion": "2024-11-05"}}
            r = integration_client.post(
                "/api/mcp/connections/validate",
                json={
                    "name": "Conn",
                    "base_url": "https://mcp.example.com",
                    "endpoint_path": "mcp",  # should be normalized to /mcp
                    "auth_type": "none",
                    "credentials": None,
                },
                headers=headers,
            )
        assert r.status_code == 200
        assert r.json()["valid"] is True

        # Create connection
        r2 = integration_client.post(
            "/api/mcp/connections",
            json={
                "name": "Conn",
                "base_url": "https://mcp.example.com",
                "endpoint_path": "mcp",
                "auth_type": "none",
                "credentials": {"token": "secret"},
            },
            headers=headers,
        )
        assert r2.status_code == 201, r2.text
        conn = r2.json()
        assert conn["endpoint_path"].startswith("/")
        conn_id = conn["id"]

        # List/get
        r3 = integration_client.get("/api/mcp/connections", headers=headers)
        assert r3.status_code == 200
        assert any(c["id"] == conn_id for c in r3.json())

        r4 = integration_client.get(f"/api/mcp/connections/{conn_id}", headers=headers)
        assert r4.status_code == 200

        # Patch (deactivate)
        r5 = integration_client.patch(
            f"/api/mcp/connections/{conn_id}",
            json={"is_active": False},
            headers=headers,
        )
        assert r5.status_code == 200
        assert r5.json()["is_active"] is False

        # Delete
        r6 = integration_client.delete(f"/api/mcp/connections/{conn_id}", headers=headers)
        assert r6.status_code == 204

    def test_mcp_tool_crud_and_job_scoping(self, integration_client: TestClient, business_user):
        headers = _auth_headers(business_user)

        # Validate tool config (filesystem) without touching network
        r0 = integration_client.post(
            "/api/mcp/tools/validate",
            json={"tool_type": "filesystem", "config": {"base_path": "."}},
            headers=headers,
        )
        assert r0.status_code == 200
        assert "valid" in r0.json()

        # Create a platform tool config
        r1 = integration_client.post(
            "/api/mcp/tools",
            json={
                "tool_type": "filesystem",
                "name": "FS",
                "config": {"base_path": "."},
                "business_description": "Local files",
            },
            headers=headers,
        )
        assert r1.status_code == 201, r1.text
        tool = r1.json()
        tool_id = tool["id"]

        # List/get tool
        r2 = integration_client.get("/api/mcp/tools", headers=headers)
        assert r2.status_code == 200
        assert any(t["id"] == tool_id for t in r2.json())

        r3 = integration_client.get(f"/api/mcp/tools/{tool_id}", headers=headers)
        assert r3.status_code == 200

        # Create a job scoped to allowed_platform_tool_ids (as JSON form string)
        r4 = integration_client.post(
            "/api/jobs",
            data={
                "title": "Scoped Job",
                "allowed_platform_tool_ids": json.dumps([tool_id]),
                "tool_visibility": "names_only",
            },
            headers=headers,
        )
        assert r4.status_code == 201, r4.text
        job = r4.json()
        job_id = job["id"]
        assert job["allowed_platform_tool_ids"] == [tool_id]
        assert job["tool_visibility"] == "names_only"

        # Update job scope (explicit empty list means none)
        r5 = integration_client.put(
            f"/api/jobs/{job_id}",
            data={"allowed_platform_tool_ids": json.dumps([]), "tool_visibility": "full"},
            headers=headers,
        )
        assert r5.status_code == 200, r5.text
        updated = r5.json()
        assert updated["tool_visibility"] == "full"

        # Delete tool
        r6 = integration_client.delete(f"/api/mcp/tools/{tool_id}", headers=headers)
        assert r6.status_code == 204

    def test_mcp_registry_and_proxy(self, integration_client: TestClient, business_user):
        """
        Covers:
        - GET /api/mcp/registry (with mocked external MCP tools/list)
        - POST /api/mcp/proxy (with mocked JSON-RPC forward)
        """
        headers = _auth_headers(business_user)

        # Create an active platform tool so platform_tools is non-empty
        r_tool = integration_client.post(
            "/api/mcp/tools",
            json={
                "tool_type": "filesystem",
                "name": "FS Registry",
                "config": {"base_path": "."},
                "business_description": "Local files",
            },
            headers=headers,
        )
        assert r_tool.status_code == 201, r_tool.text

        # Create an active external connection so connection_tools is non-empty
        r_conn = integration_client.post(
            "/api/mcp/connections",
            json={
                "name": "External MCP",
                "base_url": "https://mcp.example.com",
                "endpoint_path": "/mcp",
                "auth_type": "none",
                "credentials": {"token": "secret"},
            },
            headers=headers,
        )
        assert r_conn.status_code == 201, r_conn.text
        conn_id = r_conn.json()["id"]

        # Registry: mock tools/list call for external connection
        with patch("services.mcp_client.list_tools", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = {"tools": [{"name": "toolA", "description": "desc"}]}
            r_reg = integration_client.get("/api/mcp/registry", headers=headers)
            r_reg_post = integration_client.post("/api/mcp/registry", headers=headers)
        assert r_reg.status_code == 200, r_reg.text
        body = r_reg.json()
        assert body["platform_tool_count"] >= 1
        assert body["platform_tools"]
        assert body["connection_tools"]
        assert body["connection_tools"][0]["tools"][0]["name"] == "toolA"
        assert r_reg_post.status_code == 200, r_reg_post.text
        assert r_reg_post.json()["connection_tools"][0]["tools"][0]["name"] == "toolA"

        # Proxy: mock JSON-RPC forward
        with patch("services.mcp_client.call_mcp_server", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"jsonrpc": "2.0", "result": {"ok": True}}
            r_proxy = integration_client.post(
                "/api/mcp/proxy",
                json={"connection_id": conn_id, "method": "tools/list", "params": {}},
                headers=headers,
            )
        assert r_proxy.status_code == 200, r_proxy.text
        assert r_proxy.json()["result"]["ok"] is True

