"""
Unit tests for internal MCP API (used by platform MCP server).
Endpoints: GET /api/internal/mcp/tools, POST /api/internal/mcp/tools/{id}/config.
Protected by X-Internal-Secret header.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from core.security import get_password_hash, create_access_token
from db.database import get_db
from main import app
from models.user import User, UserRole
from models.mcp_server import MCPToolConfig, MCPToolType


@pytest.fixture
def internal_secret():
    return "test-internal-secret-123"


@pytest.fixture
def client_internal(db_session, business_user, internal_secret):
    """Override get_db and MCP_INTERNAL_SECRET so internal routes accept the test secret."""
    from core import config
    original_secret = getattr(config.settings, "MCP_INTERNAL_SECRET", None)
    config.settings.MCP_INTERNAL_SECRET = internal_secret

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    if original_secret is not None:
        config.settings.MCP_INTERNAL_SECRET = original_secret
    else:
        config.settings.MCP_INTERNAL_SECRET = ""


@pytest.fixture
def business_user(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"internal-mcp-{unique}@test.com",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def platform_tool(db_session, business_user):
    """One platform tool config (postgres) for the business user."""
    from core.encryption import encrypt_json
    tool = MCPToolConfig(
        user_id=business_user.id,
        tool_type=MCPToolType.POSTGRES,
        name="Test DB",
        encrypted_config=encrypt_json({"connection_string": "postgresql://u:p@localhost/db"}),
        is_active=True,
    )
    db_session.add(tool)
    db_session.commit()
    db_session.refresh(tool)
    return tool


class TestInternalMCPAuth:
    """Internal API requires X-Internal-Secret."""

    def test_list_tools_without_secret_returns_403(self, client_internal):
        r = client_internal.get("/api/internal/mcp/tools?business_id=1")
        assert r.status_code == 403

    def test_list_tools_with_wrong_secret_returns_403(self, client_internal, internal_secret):
        r = client_internal.get(
            "/api/internal/mcp/tools?business_id=1",
            headers={"X-Internal-Secret": "wrong-secret"},
        )
        assert r.status_code == 403

    def test_get_config_without_secret_returns_403(self, client_internal, platform_tool):
        r = client_internal.post(
            f"/api/internal/mcp/tools/{platform_tool.id}/config",
            json={"business_id": platform_tool.user_id},
        )
        assert r.status_code == 403


class TestInternalMCPListTools:
    """GET /api/internal/mcp/tools?business_id=N."""

    def test_list_tools_empty(self, client_internal, internal_secret, business_user):
        r = client_internal.get(
            f"/api/internal/mcp/tools?business_id={business_user.id}",
            headers={"X-Internal-Secret": internal_secret},
        )
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        assert data["tools"] == []

    def test_list_tools_returns_tool_descriptor(self, client_internal, internal_secret, platform_tool):
        business_id = platform_tool.user_id
        r = client_internal.get(
            f"/api/internal/mcp/tools?business_id={business_id}",
            headers={"X-Internal-Secret": internal_secret},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["tools"]) == 1
        t = data["tools"][0]
        assert t["id"] == platform_tool.id
        assert "platform_" in t["name"]
        assert "inputSchema" in t
        assert t["inputSchema"]["required"] == []

    def test_list_tools_excludes_inactive(self, db_session, client_internal, internal_secret, business_user):
        from core.encryption import encrypt_json
        tool = MCPToolConfig(
            user_id=business_user.id,
            tool_type=MCPToolType.FILESYSTEM,
            name="Inactive FS",
            encrypted_config=encrypt_json({"base_path": "/tmp"}),
            is_active=False,
        )
        db_session.add(tool)
        db_session.commit()
        r = client_internal.get(
            f"/api/internal/mcp/tools?business_id={business_user.id}",
            headers={"X-Internal-Secret": internal_secret},
        )
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["tools"]]
        assert "Inactive FS" not in names or not any("Inactive" in n for n in names)

    def test_list_tools_includes_write_capable_schema_for_snowflake(self, db_session, client_internal, internal_secret, business_user):
        from core.encryption import encrypt_json
        tool = MCPToolConfig(
            user_id=business_user.id,
            tool_type=MCPToolType.SNOWFLAKE,
            name="Snowflake DW",
            encrypted_config=encrypt_json({"account": "a", "warehouse": "w"}),
            is_active=True,
        )
        db_session.add(tool)
        db_session.commit()
        r = client_internal.get(
            f"/api/internal/mcp/tools?business_id={business_user.id}",
            headers={"X-Internal-Secret": internal_secret},
        )
        assert r.status_code == 200
        entries = r.json()["tools"]
        sf = next((x for x in entries if x["id"] == tool.id), None)
        assert sf is not None
        assert "inputSchema" in sf
        props = sf["inputSchema"].get("properties", {})
        assert "operation_type" in props
        assert "merge_keys" in props


class TestInternalMCPGetConfig:
    """POST /api/internal/mcp/tools/{tool_id}/config with body { business_id }."""

    def test_get_config_success(self, client_internal, internal_secret, platform_tool):
        r = client_internal.post(
            f"/api/internal/mcp/tools/{platform_tool.id}/config",
            headers={"X-Internal-Secret": internal_secret},
            json={"business_id": platform_tool.user_id},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["tool_id"] == platform_tool.id
        assert data["tool_type"] == "postgres"
        assert data["name"] == "Test DB"
        assert "config" in data
        assert data["config"].get("connection_string") == "postgresql://u:p@localhost/db"

    def test_get_config_wrong_business_returns_404(self, client_internal, internal_secret, platform_tool):
        r = client_internal.post(
            f"/api/internal/mcp/tools/{platform_tool.id}/config",
            headers={"X-Internal-Secret": internal_secret},
            json={"business_id": 99999},
        )
        assert r.status_code == 404

    def test_get_config_nonexistent_tool_returns_404(self, client_internal, internal_secret, business_user):
        r = client_internal.post(
            "/api/internal/mcp/tools/99999/config",
            headers={"X-Internal-Secret": internal_secret},
            json={"business_id": business_user.id},
        )
        assert r.status_code == 404
