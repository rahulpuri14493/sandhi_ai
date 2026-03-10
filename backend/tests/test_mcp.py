"""API tests for MCP routes: connections, tools, validate, registry."""
import uuid

import pytest
from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from db.database import get_db
from main import app
from models.user import User, UserRole


@pytest.fixture
def business_user(db_session):
    """Create a business user and return user + auth headers."""
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"mcp-business-{unique}@test.com",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(data={"sub": user.id})
    return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def developer_user(db_session):
    """Create a developer user (MCP routes require business role)."""
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"mcp-dev-{unique}@test.com",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(data={"sub": user.id})
    return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def client_mcp(db_session, business_user):
    """Test client with DB override and business user (use business_user['headers'] for auth)."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestMCPAuth:
    """MCP endpoints require business user."""

    def test_list_connections_requires_auth(self, client: TestClient):
        r = client.get("/api/mcp/connections")
        assert r.status_code == 401

    def test_list_tools_requires_auth(self, client: TestClient):
        r = client.get("/api/mcp/tools")
        assert r.status_code == 401

    def test_list_connections_requires_business_role(self, client_mcp: TestClient, developer_user):
        r = client_mcp.get("/api/mcp/connections", headers=developer_user["headers"])
        assert r.status_code == 403

    def test_list_connections_success(self, client_mcp: TestClient, business_user):
        r = client_mcp.get("/api/mcp/connections", headers=business_user["headers"])
        assert r.status_code == 200
        assert r.json() == []

    def test_list_tools_success(self, client_mcp: TestClient, business_user):
        r = client_mcp.get("/api/mcp/tools", headers=business_user["headers"])
        assert r.status_code == 200
        assert r.json() == []


class TestMCPConnections:
    """CRUD for MCP server connections."""

    def test_create_connection(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={
                "name": "Test MCP Server",
                "base_url": "https://mcp.example.com",
                "endpoint_path": "/mcp",
                "auth_type": "none",
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Test MCP Server"
        assert data["base_url"] == "https://mcp.example.com"
        assert data["endpoint_path"] == "/mcp"
        assert "id" in data

    def test_create_connection_normalizes_endpoint_path(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={
                "name": "Test",
                "base_url": "https://mcp.example.com",
                "endpoint_path": "mcp",
            },
        )
        assert r.status_code == 201
        assert r.json()["endpoint_path"] == "/mcp"

    def test_list_connections_after_create(self, client_mcp: TestClient, business_user):
        client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={"name": "C1", "base_url": "https://a.com", "auth_type": "none"},
        )
        r = client_mcp.get("/api/mcp/connections", headers=business_user["headers"])
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["name"] == "C1"

    def test_delete_connection(self, client_mcp: TestClient, business_user):
        cr = client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={"name": "ToDelete", "base_url": "https://x.com", "auth_type": "none"},
        )
        cid = cr.json()["id"]
        r = client_mcp.delete(f"/api/mcp/connections/{cid}", headers=business_user["headers"])
        assert r.status_code == 204
        r2 = client_mcp.get("/api/mcp/connections", headers=business_user["headers"])
        assert len(r2.json()) == 0


class TestMCPTools:
    """CRUD for platform MCP tool configs."""

    def test_create_tool(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={
                "tool_type": "postgres",
                "name": "My DB",
                "config": {"connection_string": "postgresql://u:p@localhost/db"},
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "My DB"
        assert data["tool_type"] == "postgres"
        assert "id" in data
        assert "encrypted_config" not in data

    def test_create_tool_invalid_type(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "invalid_type", "name": "X", "config": {}},
        )
        assert r.status_code == 400

    def test_list_tools_after_create(self, client_mcp: TestClient, business_user):
        client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "filesystem", "name": "FS1", "config": {"base_path": "/tmp"}},
        )
        r = client_mcp.get("/api/mcp/tools", headers=business_user["headers"])
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["name"] == "FS1"
        assert r.json()[0]["tool_type"] == "filesystem"

    def test_update_tool(self, client_mcp: TestClient, business_user):
        cr = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "rest_api", "name": "API1", "config": {"base_url": "https://api.example.com"}},
        )
        tid = cr.json()["id"]
        r = client_mcp.patch(
            f"/api/mcp/tools/{tid}",
            headers=business_user["headers"],
            json={"name": "API1 Updated"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "API1 Updated"

    def test_delete_tool(self, client_mcp: TestClient, business_user):
        cr = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "vector_db", "name": "V1", "config": {}},
        )
        tid = cr.json()["id"]
        r = client_mcp.delete(f"/api/mcp/tools/{tid}", headers=business_user["headers"])
        assert r.status_code == 204
        r2 = client_mcp.get("/api/mcp/tools", headers=business_user["headers"])
        assert len(r2.json()) == 0


class TestMCPConnectionValidate:
    """POST /api/mcp/connections/validate."""

    def test_validate_connection_requires_auth(self, client: TestClient):
        r = client.post(
            "/api/mcp/connections/validate",
            json={"name": "Test", "base_url": "https://mcp.example.com", "auth_type": "none"},
        )
        assert r.status_code == 401

    def test_validate_connection_missing_url(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/connections/validate",
            headers=business_user["headers"],
            json={"name": "Test", "base_url": "", "auth_type": "none"},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is False
        assert "required" in r.json()["message"].lower()

    def test_validate_connection_unreachable_returns_false(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/connections/validate",
            headers=business_user["headers"],
            json={
                "name": "Test",
                "base_url": "https://nonexistent-mcp-host-xyz.invalid",
                "endpoint_path": "/mcp",
                "auth_type": "none",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert data["message"]


class TestMCPValidate:
    """POST /api/mcp/tools/validate."""

    def test_validate_requires_auth(self, client: TestClient):
        r = client.post(
            "/api/mcp/tools/validate",
            json={"tool_type": "postgres", "config": {"connection_string": "postgresql://x/y"}},
        )
        assert r.status_code == 401

    def test_validate_postgres_missing_connection_string(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/tools/validate",
            headers=business_user["headers"],
            json={"tool_type": "postgres", "config": {}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert "Connection string is required" in data["message"]

    def test_validate_filesystem_valid_path(self, client_mcp: TestClient, business_user, tmp_path):
        r = client_mcp.post(
            "/api/mcp/tools/validate",
            headers=business_user["headers"],
            json={"tool_type": "filesystem", "config": {"base_path": str(tmp_path)}},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_validate_invalid_tool_type(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/tools/validate",
            headers=business_user["headers"],
            json={"tool_type": "invalid", "config": {}},
        )
        assert r.status_code == 400


class TestMCPRegistry:
    """GET /api/mcp/registry."""

    def test_registry_requires_auth(self, client: TestClient):
        r = client.get("/api/mcp/registry")
        assert r.status_code == 401

    def test_registry_success(self, client_mcp: TestClient, business_user):
        r = client_mcp.get("/api/mcp/registry", headers=business_user["headers"])
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            data = r.json()
            assert "tools" in data


# ---------- Positive test cases (expected success) ----------


class TestMCPPositive:
    """Positive: valid operations succeed and return expected shape."""

    def test_positive_create_connection_and_get(self, client_mcp: TestClient, business_user):
        cr = client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={"name": "GetMe", "base_url": "https://mcp.example.com", "auth_type": "none"},
        )
        assert cr.status_code == 201
        cid = cr.json()["id"]
        r = client_mcp.get(f"/api/mcp/connections/{cid}", headers=business_user["headers"])
        assert r.status_code == 200
        assert r.json()["name"] == "GetMe"

    def test_positive_create_tool_and_get(self, client_mcp: TestClient, business_user):
        cr = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "rest_api", "name": "GetMeTool", "config": {"base_url": "https://api.example.com"}},
        )
        assert cr.status_code == 201
        tid = cr.json()["id"]
        r = client_mcp.get(f"/api/mcp/tools/{tid}", headers=business_user["headers"])
        assert r.status_code == 200
        assert r.json()["name"] == "GetMeTool"

    def test_positive_validate_tool_filesystem_success(self, client_mcp: TestClient, business_user, tmp_path):
        r = client_mcp.post(
            "/api/mcp/tools/validate",
            headers=business_user["headers"],
            json={"tool_type": "filesystem", "config": {"base_path": str(tmp_path)}},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is True


# ---------- Negative test cases (expected failure or 4xx) ----------


class TestMCPNegative:
    """Negative: invalid requests return 4xx or expected error response."""

    def test_negative_get_connection_404(self, client_mcp: TestClient, business_user):
        r = client_mcp.get("/api/mcp/connections/99999", headers=business_user["headers"])
        assert r.status_code == 404
        assert "not found" in r.json().get("detail", "").lower()

    def test_negative_get_tool_404(self, client_mcp: TestClient, business_user):
        r = client_mcp.get("/api/mcp/tools/99999", headers=business_user["headers"])
        assert r.status_code == 404
        assert "not found" in r.json().get("detail", "").lower()

    def test_negative_update_tool_404(self, client_mcp: TestClient, business_user):
        r = client_mcp.patch(
            "/api/mcp/tools/99999",
            headers=business_user["headers"],
            json={"name": "Updated"},
        )
        assert r.status_code == 404

    def test_negative_delete_connection_404(self, client_mcp: TestClient, business_user):
        r = client_mcp.delete("/api/mcp/connections/99999", headers=business_user["headers"])
        assert r.status_code == 404

    def test_negative_delete_tool_404(self, client_mcp: TestClient, business_user):
        r = client_mcp.delete("/api/mcp/tools/99999", headers=business_user["headers"])
        assert r.status_code == 404

    def test_negative_create_connection_missing_base_url(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={"name": "NoUrl", "auth_type": "none"},
        )
        assert r.status_code == 422

    def test_negative_create_tool_invalid_type(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "invalid_kind", "name": "X", "config": {}},
        )
        assert r.status_code == 400

    def test_negative_validate_tool_invalid_type(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/tools/validate",
            headers=business_user["headers"],
            json={"tool_type": "invalid_type", "config": {}},
        )
        assert r.status_code == 400
