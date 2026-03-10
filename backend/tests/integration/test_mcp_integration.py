"""
Integration tests for MCP flows: create connection, create tool, list, update, delete.
Uses integration_client and business_user from integration/conftest.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_headers(business_user):
    return {"Authorization": f"Bearer {business_user['token']}"}


class TestMCPIntegrationFlow:
    """Full MCP flow as a business user."""

    def test_connection_crud_flow(self, integration_client: TestClient, auth_headers):
        """Create connection -> list -> get -> update -> delete."""
        # Create
        r = integration_client.post(
            "/api/mcp/connections",
            headers=auth_headers,
            json={
                "name": "E2E MCP Server",
                "base_url": "https://mcp-e2e.example.com",
                "endpoint_path": "/mcp",
                "auth_type": "bearer",
                "credentials": {"token": "secret-token"},
            },
        )
        assert r.status_code == 201, r.text
        conn = r.json()
        cid = conn["id"]
        assert conn["name"] == "E2E MCP Server"
        assert conn["base_url"] == "https://mcp-e2e.example.com"

        # List
        r2 = integration_client.get("/api/mcp/connections", headers=auth_headers)
        assert r2.status_code == 200
        list_data = r2.json()
        assert any(c["id"] == cid for c in list_data)

        # Get one
        r3 = integration_client.get(f"/api/mcp/connections/{cid}", headers=auth_headers)
        assert r3.status_code == 200
        assert r3.json()["name"] == "E2E MCP Server"

        # Update
        r4 = integration_client.patch(
            f"/api/mcp/connections/{cid}",
            headers=auth_headers,
            json={"name": "E2E MCP Server Updated"},
        )
        assert r4.status_code == 200
        assert r4.json()["name"] == "E2E MCP Server Updated"

        # Delete
        r5 = integration_client.delete(f"/api/mcp/connections/{cid}", headers=auth_headers)
        assert r5.status_code == 204
        r6 = integration_client.get(f"/api/mcp/connections/{cid}", headers=auth_headers)
        assert r6.status_code == 404

    def test_tool_crud_flow(self, integration_client: TestClient, auth_headers):
        """Create tool -> list -> get -> update -> delete."""
        # Create
        r = integration_client.post(
            "/api/mcp/tools",
            headers=auth_headers,
            json={
                "tool_type": "postgres",
                "name": "E2E Postgres",
                "config": {"connection_string": "postgresql://u:p@host/db", "schema": "public"},
            },
        )
        assert r.status_code == 201, r.text
        tool = r.json()
        tid = tool["id"]
        assert tool["name"] == "E2E Postgres"
        assert tool["tool_type"] == "postgres"

        # List
        r2 = integration_client.get("/api/mcp/tools", headers=auth_headers)
        assert r2.status_code == 200
        assert any(t["id"] == tid for t in r2.json())

        # Get one
        r3 = integration_client.get(f"/api/mcp/tools/{tid}", headers=auth_headers)
        assert r3.status_code == 200
        assert r3.json()["name"] == "E2E Postgres"

        # Update name only (config optional on update)
        r4 = integration_client.patch(
            f"/api/mcp/tools/{tid}",
            headers=auth_headers,
            json={"name": "E2E Postgres Renamed"},
        )
        assert r4.status_code == 200
        assert r4.json()["name"] == "E2E Postgres Renamed"

        # Delete
        r5 = integration_client.delete(f"/api/mcp/tools/{tid}", headers=auth_headers)
        assert r5.status_code == 204
        r6 = integration_client.get(f"/api/mcp/tools/{tid}", headers=auth_headers)
        assert r6.status_code == 404

    def test_validate_then_create_tool(self, integration_client: TestClient, auth_headers, tmp_path):
        """Validate filesystem config then create tool (no real connection)."""
        r = integration_client.post(
            "/api/mcp/tools/validate",
            headers=auth_headers,
            json={"tool_type": "filesystem", "config": {"base_path": str(tmp_path)}},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is True

        r2 = integration_client.post(
            "/api/mcp/tools",
            headers=auth_headers,
            json={
                "tool_type": "filesystem",
                "name": "E2E FS",
                "config": {"base_path": str(tmp_path)},
            },
        )
        assert r2.status_code == 201
        assert r2.json()["name"] == "E2E FS"
