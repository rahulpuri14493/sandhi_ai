"""API tests for MCP routes: connections, tools, validate, registry."""
import uuid
from unittest.mock import AsyncMock, patch

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

    def test_create_tool_all_vector_types(self, client_mcp: TestClient, business_user):
        """Create one tool per vector/store type; all accepted by API."""
        types_and_configs = [
            ("chroma", {"url": "http://localhost:8000", "index_name": "col"}),
            ("pinecone", {"api_key": "k", "host": "https://x.pinecone.io"}),
            ("weaviate", {"url": "http://localhost:8080", "index_name": "X"}),
            ("qdrant", {"url": "http://localhost:6333", "index_name": "Y"}),
            ("vector_db", {"url": "https://v.example.com", "api_key": "k"}),
        ]
        for tool_type, config in types_and_configs:
            r = client_mcp.post(
                "/api/mcp/tools",
                headers=business_user["headers"],
                json={"tool_type": tool_type, "name": f"V-{tool_type}", "config": config},
            )
            assert r.status_code == 201, f"create {tool_type}: {r.text}"
            assert r.json()["tool_type"] == tool_type

    def test_create_tool_integration_types(self, client_mcp: TestClient, business_user):
        """Create tools for s3, slack, github, notion, elasticsearch, mysql."""
        types_and_configs = [
            ("s3", {"bucket": "b", "region": "us-east-1"}),
            ("slack", {"token": "xoxb-x"}),
            ("github", {"token": "ghp-x"}),
            ("notion", {"api_key": "secret-x"}),
            ("elasticsearch", {"url": "http://localhost:9200"}),
            ("mysql", {"host": "localhost", "user": "u", "password": "p", "database": "d"}),
        ]
        for tool_type, config in types_and_configs:
            r = client_mcp.post(
                "/api/mcp/tools",
                headers=business_user["headers"],
                json={"tool_type": tool_type, "name": f"I-{tool_type}", "config": config},
            )
            assert r.status_code == 201, f"create {tool_type}: {r.text}"
            assert r.json()["tool_type"] == tool_type

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

    def test_update_tool_merges_config_instead_of_replacing(self, client_mcp: TestClient, business_user, db_session):
        from core.encryption import decrypt_json
        from models.mcp_server import MCPToolConfig

        cr = client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={
                "tool_type": "postgres",
                "name": "PG Merge",
                "config": {
                    "connection_string": "postgresql://u:p@localhost/db",
                    "schema": "public",
                    "query": "SELECT now();",
                },
            },
        )
        assert cr.status_code == 201
        tid = cr.json()["id"]

        # Partial PATCH should preserve existing query/connection_string.
        r = client_mcp.patch(
            f"/api/mcp/tools/{tid}",
            headers=business_user["headers"],
            json={"config": {"schema": "analytics"}},
        )
        assert r.status_code == 200

        row = db_session.query(MCPToolConfig).filter(MCPToolConfig.id == tid).first()
        assert row is not None
        cfg = decrypt_json(row.encrypted_config)
        assert cfg["schema"] == "analytics"
        assert cfg["query"] == "SELECT now();"
        assert cfg["connection_string"] == "postgresql://u:p@localhost/db"

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
            assert "platform_tool_count" in data
            assert isinstance(data["tools"], list)

    def test_registry_includes_platform_tools(self, client_mcp: TestClient, business_user):
        client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "chroma", "name": "My Chroma", "config": {"url": "http://localhost:8000"}},
        )
        r = client_mcp.get("/api/mcp/registry", headers=business_user["headers"])
        assert r.status_code == 200
        data = r.json()
        assert data["platform_tool_count"] >= 1
        platform_tools = [t for t in data["tools"] if t.get("source") == "platform"]
        assert len(platform_tools) >= 1
        assert any("platform_" in t["name"] for t in platform_tools)
        assert any(t.get("tool_type") == "chroma" for t in platform_tools)
        chroma_entries = [t for t in data["platform_tools"] if t.get("tool_type") == "chroma"]
        assert chroma_entries and chroma_entries[0].get("access_mode") == "read_only"

    def test_registry_platform_tool_read_write_slack(self, client_mcp: TestClient, business_user):
        client_mcp.post(
            "/api/mcp/tools",
            headers=business_user["headers"],
            json={"tool_type": "slack", "name": "Slack RW", "config": {}},
        )
        r = client_mcp.get("/api/mcp/registry", headers=business_user["headers"])
        assert r.status_code == 200
        slack_entries = [t for t in r.json().get("platform_tools", []) if t.get("tool_type") == "slack"]
        assert slack_entries and slack_entries[0].get("access_mode") == "read_write"


class TestMCPProxy:
    """POST /api/mcp/proxy - forward JSON-RPC to user's MCP server."""

    def test_proxy_requires_auth(self, client: TestClient):
        r = client.post("/api/mcp/proxy", json={"connection_id": 1, "method": "initialize", "params": {}})
        assert r.status_code == 401

    def test_proxy_connection_not_found(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/proxy",
            headers=business_user["headers"],
            json={"connection_id": 99999, "method": "initialize", "params": {}},
        )
        assert r.status_code == 404
        assert "not found" in r.json().get("detail", "").lower()

    def test_proxy_connection_inactive_returns_404(self, client_mcp: TestClient, business_user, db_session):
        from models.mcp_server import MCPServerConnection
        conn = MCPServerConnection(
            user_id=business_user["user"].id,
            name="Inactive",
            base_url="https://mcp.example.com",
            endpoint_path="/mcp",
            auth_type="none",
            is_active=False,
        )
        db_session.add(conn)
        db_session.commit()
        db_session.refresh(conn)
        r = client_mcp.post(
            "/api/mcp/proxy",
            headers=business_user["headers"],
            json={"connection_id": conn.id, "method": "initialize", "params": {}},
        )
        assert r.status_code == 404


class TestMCPCallPlatformTool:
    """POST /api/mcp/call-platform-tool - invoke platform MCP tool by name."""

    def test_call_platform_tool_requires_auth(self, client: TestClient):
        r = client.post(
            "/api/mcp/call-platform-tool",
            json={"tool_name": "platform_1_MyDB", "arguments": {"query": "SELECT 1"}},
        )
        assert r.status_code == 401

    def test_call_platform_tool_not_configured_returns_503(self, client_mcp: TestClient, business_user):
        """When PLATFORM_MCP_SERVER_URL or MCP_INTERNAL_SECRET unset, returns 503."""
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        config.settings.PLATFORM_MCP_SERVER_URL = ""
        config.settings.MCP_INTERNAL_SECRET = ""
        try:
            r = client_mcp.post(
                "/api/mcp/call-platform-tool",
                headers=business_user["headers"],
                json={"tool_name": "platform_1_MyDB", "arguments": {"query": "SELECT 1"}},
            )
            assert r.status_code == 503
            assert "not configured" in r.json().get("detail", "").lower()
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret

    def test_call_platform_tool_rejects_oversized_arguments(self, client_mcp: TestClient, business_user):
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        orig_max = getattr(config.settings, "MCP_TOOL_MAX_ARGUMENT_BYTES", 5242880)
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        config.settings.MCP_TOOL_MAX_ARGUMENT_BYTES = 32
        try:
            r = client_mcp.post(
                "/api/mcp/call-platform-tool",
                headers=business_user["headers"],
                json={"tool_name": "platform_1_t", "arguments": {"payload": "x" * 1024}},
            )
            assert r.status_code == 413
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret
            config.settings.MCP_TOOL_MAX_ARGUMENT_BYTES = orig_max

    def test_call_platform_write_requires_merge_keys_for_upsert(self, client_mcp: TestClient, business_user):
        r = client_mcp.post(
            "/api/mcp/call-platform-write",
            headers=business_user["headers"],
            json={
                "tool_name": "platform_1_snowflake",
                "artifact_ref": {"storage": "s3", "path": "jobs/1/out.parquet", "format": "parquet"},
                "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": [],
                "idempotency_key": "run-1-step-1-shard-1",
            },
        )
        assert r.status_code == 422

    def test_call_platform_write_success_passes_normalized_arguments(self, client_mcp: TestClient, business_user):
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        try:
            with patch("services.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"content": [{"type": "text", "text": "ok"}], "isError": False}
                r = client_mcp.post(
                    "/api/mcp/call-platform-write",
                    headers=business_user["headers"],
                    json={
                        "tool_name": "platform_1_snowflake",
                        "artifact_ref": {"storage": "s3", "path": "jobs/1/out.parquet", "format": "parquet"},
                        "target": {
                            "target_type": "snowflake",
                            "name": "analytics.kyc_decisions",
                            "database": "ANALYTICS",
                            "schema": "PUBLIC",
                            "table": "KYC_DECISIONS",
                        },
                        "operation_type": "upsert",
                        "write_mode": "upsert",
                        "merge_keys": ["customer_id", "as_of_date"],
                        "idempotency_key": "run-1-step-1-shard-1",
                    },
                )
            assert r.status_code == 200, r.text
            called_kwargs = mock_call.await_args.kwargs
            assert called_kwargs["tool_name"] == "platform_1_snowflake"
            assert called_kwargs["arguments"]["operation_type"] == "upsert"
            assert called_kwargs["arguments"]["merge_keys"] == ["customer_id", "as_of_date"]
            assert called_kwargs["arguments"]["idempotency_key"] == "run-1-step-1-shard-1"
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret

    def test_call_platform_write_async_returns_operation(self, client_mcp: TestClient, business_user):
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        try:
            with patch("services.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"content": [{"type": "text", "text": "ok"}], "isError": False}
                r = client_mcp.post(
                    "/api/mcp/call-platform-write-async",
                    headers=business_user["headers"],
                    json={
                        "tool_name": "platform_1_snowflake",
                        "artifact_ref": {"storage": "s3", "path": "jobs/1/out.parquet", "format": "parquet"},
                        "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                        "operation_type": "upsert",
                        "write_mode": "upsert",
                        "merge_keys": ["customer_id"],
                        "idempotency_key": "job-1-step-1-shard-1",
                    },
                )
            assert r.status_code == 202, r.text
            body = r.json()
            assert body["operation_id"].startswith("op_")
            assert body["status"] in ("accepted", "in_progress", "success")
            # Poll operation endpoint
            r2 = client_mcp.get(f"/api/mcp/operations/{body['operation_id']}", headers=business_user["headers"])
            assert r2.status_code == 200
            assert r2.json()["operation_id"] == body["operation_id"]
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret

    def test_call_platform_write_async_idempotency_reuses_operation(self, client_mcp: TestClient, business_user):
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        try:
            with patch("services.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"content": [{"type": "text", "text": "ok"}], "isError": False}
                payload = {
                    "tool_name": "platform_1_snowflake",
                    "artifact_ref": {"storage": "s3", "path": "jobs/1/out.parquet", "format": "parquet"},
                    "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                    "operation_type": "upsert",
                    "write_mode": "upsert",
                    "merge_keys": ["customer_id"],
                    "idempotency_key": "same-idempotency-key-12345",
                }
                r1 = client_mcp.post("/api/mcp/call-platform-write-async", headers=business_user["headers"], json=payload)
                r2 = client_mcp.post("/api/mcp/call-platform-write-async", headers=business_user["headers"], json=payload)
            assert r1.status_code == 202
            assert r2.status_code == 202
            assert r1.json()["operation_id"] == r2.json()["operation_id"]
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret

    def test_call_platform_write_async_retries_then_succeeds(self, client_mcp: TestClient, business_user):
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        orig_attempts = getattr(config.settings, "MCP_WRITE_OPERATION_MAX_ATTEMPTS", 3)
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        config.settings.MCP_WRITE_OPERATION_MAX_ATTEMPTS = 3
        try:
            with patch("services.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
                mock_call.side_effect = [RuntimeError("transient"), {"content": [{"type": "text", "text": "ok"}], "isError": False}]
                r = client_mcp.post(
                    "/api/mcp/call-platform-write-async",
                    headers=business_user["headers"],
                    json={
                        "tool_name": "platform_1_snowflake",
                        "artifact_ref": {"storage": "s3", "path": "jobs/1/out.parquet", "format": "parquet"},
                        "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                        "operation_type": "upsert",
                        "write_mode": "upsert",
                        "merge_keys": ["customer_id"],
                        "idempotency_key": "retry-idempotency-key-123",
                    },
                )
            assert r.status_code == 202
            op_id = r.json()["operation_id"]
            r2 = client_mcp.get(f"/api/mcp/operations/{op_id}", headers=business_user["headers"])
            assert r2.status_code == 200
            assert r2.json()["status"] in ("success", "in_progress", "accepted")
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret
            config.settings.MCP_WRITE_OPERATION_MAX_ATTEMPTS = orig_attempts

    def test_call_platform_write_async_high_volume_submit_smoke(self, client_mcp: TestClient, business_user):
        from core import config
        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        try:
            with patch("services.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"content": [{"type": "text", "text": "ok"}], "isError": False}
                op_ids = set()
                for i in range(20):
                    r = client_mcp.post(
                        "/api/mcp/call-platform-write-async",
                        headers=business_user["headers"],
                        json={
                            "tool_name": "platform_1_snowflake",
                            "artifact_ref": {"storage": "s3", "path": f"jobs/1/out_{i}.parquet", "format": "parquet"},
                            "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                            "operation_type": "upsert",
                            "write_mode": "upsert",
                            "merge_keys": ["customer_id"],
                            "idempotency_key": f"bulk-idempotency-{i}",
                        },
                    )
                    assert r.status_code == 202
                    op_ids.add(r.json()["operation_id"])
                assert len(op_ids) == 20
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret

    def test_call_platform_write_async_burst_submissions_with_mixed_idempotency(
        self, client_mcp: TestClient, business_user
    ):
        """
        Burst submission resilience:
        - many rapid requests
        - some share idempotency keys (should dedupe)
        - some have unique keys (should create unique ops)
        """
        from core import config

        orig_url = config.settings.PLATFORM_MCP_SERVER_URL
        orig_secret = config.settings.MCP_INTERNAL_SECRET
        config.settings.PLATFORM_MCP_SERVER_URL = "http://platform-mcp-server:8081"
        config.settings.MCP_INTERNAL_SECRET = "x"
        try:
            with patch("services.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"content": [{"type": "text", "text": "ok"}], "isError": False}

                # 30 submissions: 10 duplicated (same key), 20 unique keys.
                shared_key = "parallel-shared-idempotency-key"
                payloads = []
                for i in range(10):
                    payloads.append(
                        {
                            "tool_name": "platform_1_snowflake",
                            "artifact_ref": {"storage": "s3", "path": f"jobs/1/shared_{i}.parquet", "format": "parquet"},
                            "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                            "operation_type": "upsert",
                            "write_mode": "upsert",
                            "merge_keys": ["customer_id"],
                            "idempotency_key": shared_key,
                        }
                    )
                for i in range(20):
                    payloads.append(
                        {
                            "tool_name": "platform_1_snowflake",
                            "artifact_ref": {"storage": "s3", "path": f"jobs/1/unique_{i}.parquet", "format": "parquet"},
                            "target": {"target_type": "snowflake", "name": "analytics.kyc_decisions"},
                            "operation_type": "upsert",
                            "write_mode": "upsert",
                            "merge_keys": ["customer_id"],
                            "idempotency_key": f"parallel-unique-key-{i}",
                        }
                    )

                responses = []
                for p in payloads:
                    responses.append(client_mcp.post(
                        "/api/mcp/call-platform-write-async",
                        headers=business_user["headers"],
                        json=p,
                    ))

                # Service remains healthy under concurrent load.
                assert all(r.status_code == 202 for r in responses), [r.status_code for r in responses]

                # Shared key dedupes to one operation id; unique keys remain unique.
                shared_op_ids = set()
                unique_op_ids = set()
                for r in responses:
                    body = r.json()
                    op_id = body["operation_id"]
                    if body["idempotency_key"] == shared_key:
                        shared_op_ids.add(op_id)
                    else:
                        unique_op_ids.add(op_id)
                    # Operation lookup should always resolve.
                    poll = client_mcp.get(f"/api/mcp/operations/{op_id}", headers=business_user["headers"])
                    assert poll.status_code == 200
                    assert poll.json()["operation_id"] == op_id

                assert len(shared_op_ids) == 1
                assert len(unique_op_ids) == 20
        finally:
            config.settings.PLATFORM_MCP_SERVER_URL = orig_url
            config.settings.MCP_INTERNAL_SECRET = orig_secret



class TestMCPBYOCertification:
    def test_certify_connection_success(self, client_mcp: TestClient, business_user):
        cr = client_mcp.post(
            "/api/mcp/connections",
            headers=business_user["headers"],
            json={"name": "BYO", "base_url": "https://mcp.example.com", "endpoint_path": "/mcp", "auth_type": "none"},
        )
        cid = cr.json()["id"]
        with patch("services.mcp_client.call_mcp_server", new_callable=AsyncMock) as mock_init, patch(
            "services.mcp_client.list_tools", new_callable=AsyncMock
        ) as mock_tools:
            mock_init.return_value = {"result": {"serverInfo": {"name": "x"}}}
            mock_tools.return_value = {
                "tools": [
                    {"name": "snowflake_write", "description": "write to snowflake"},
                ]
            }
            r = client_mcp.post(f"/api/mcp/connections/{cid}/certify", headers=business_user["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["certified"] is True
        assert body["recommended_policy"] == "allow_read_write"


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

    def test_positive_create_new_data_warehouse_and_object_store_tool_types(self, client_mcp: TestClient, business_user):
        for tool_type, config in [
            ("snowflake", {"account": "a", "warehouse": "w", "database": "d", "schema": "s"}),
            ("databricks", {"host": "https://dbc.example.com", "sql_warehouse_id": "wh1"}),
            ("bigquery", {"project_id": "proj", "dataset": "ds"}),
            ("sqlserver", {"host": "sql.example.com", "database": "db"}),
            ("minio", {"endpoint": "http://minio:9000", "bucket": "b"}),
            ("ceph", {"endpoint": "http://ceph:9000", "bucket": "b"}),
            ("azure_blob", {"account_url": "https://acc.blob.core.windows.net", "container": "c"}),
            ("gcs", {"project_id": "p", "bucket": "b"}),
        ]:
            r = client_mcp.post(
                "/api/mcp/tools",
                headers=business_user["headers"],
                json={"tool_type": tool_type, "name": f"T-{tool_type}", "config": config},
            )
            assert r.status_code == 201, f"{tool_type} failed: {r.text}"
            assert r.json()["tool_type"] == tool_type


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
