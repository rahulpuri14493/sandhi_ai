"""
Backend contract tests: tool names from GET /api/internal/mcp/tools must match what
the platform MCP server parses (platform_<numeric_id>_…).

Ensures list → tools/call name → config fetch by id stay aligned.
"""
import re
import uuid
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from api.routes.mcp_internal import _tool_name
from core.encryption import encrypt_json
from core.security import get_password_hash
from db.database import get_db
from main import app
from models.mcp_server import MCPToolConfig, MCPToolType
from models.user import User, UserRole


def _parse_platform_tool_id(name: str) -> Optional[int]:
    """Same rule as tools/platform_mcp_server/app.py::_parse_platform_tool_id."""
    if not name or not name.startswith("platform_"):
        return None
    match = re.match(r"^platform_(\d+)(?:_|$)", name)
    return int(match.group(1)) if match else None


@pytest.fixture
def _internal_secret():
    return "naming-contract-secret-789"


@pytest.fixture
def _business_user(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"mcp-naming-{unique}@test.com",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client_mcp_internal(db_session, _internal_secret):
    """Internal MCP routes with X-Internal-Secret (same pattern as test_mcp_internal)."""
    from core import config

    original_secret = getattr(config.settings, "MCP_INTERNAL_SECRET", None)
    config.settings.MCP_INTERNAL_SECRET = _internal_secret

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


class TestToolNameBuilder:
    """_tool_name output must be parseable by platform MCP."""

    def test_typical_name_embeds_id(self):
        assert _tool_name(42, "My Postgres") == "platform_42_My_Postgres"

    def test_special_chars_sanitized(self):
        assert _tool_name(3, "weird @ name!") == "platform_3_weird___name_"

    def test_empty_safe_suffix_uses_id_only(self):
        # Whitespace-only strips to "" so safe is empty → id-only name (not "@@@", which becomes underscores).
        assert _tool_name(9, "   ") == "platform_9"


class TestInternalListMatchesPlatformParser:
    """GET /api/internal/mcp/tools names round-trip to tool id for MCP tools/call."""

    def test_list_name_parses_to_same_id_as_row(
        self, client_mcp_internal, _internal_secret, _business_user, db_session
    ):
        tool = MCPToolConfig(
            user_id=_business_user.id,
            tool_type=MCPToolType.FILESYSTEM,
            name="Reports FS",
            encrypted_config=encrypt_json({"base_path": "/tmp"}),
            is_active=True,
        )
        db_session.add(tool)
        db_session.commit()
        db_session.refresh(tool)

        r = client_mcp_internal.get(
            f"/api/internal/mcp/tools?business_id={_business_user.id}",
            headers={"X-Internal-Secret": _internal_secret},
        )
        assert r.status_code == 200
        entries = r.json()["tools"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == tool.id
        parsed = _parse_platform_tool_id(entry["name"])
        assert parsed == tool.id
        assert entry["name"] == _tool_name(tool.id, tool.name)

    def test_config_fetch_uses_list_id(
        self, client_mcp_internal, _internal_secret, _business_user, db_session
    ):
        tool = MCPToolConfig(
            user_id=_business_user.id,
            tool_type=MCPToolType.POSTGRES,
            name="Warehouse",
            encrypted_config=encrypt_json({"connection_string": "postgresql://u:p@h/db"}),
            is_active=True,
        )
        db_session.add(tool)
        db_session.commit()
        db_session.refresh(tool)

        r_list = client_mcp_internal.get(
            f"/api/internal/mcp/tools?business_id={_business_user.id}",
            headers={"X-Internal-Secret": _internal_secret},
        )
        name = r_list.json()["tools"][0]["name"]
        assert _parse_platform_tool_id(name) == tool.id

        r_cfg = client_mcp_internal.post(
            f"/api/internal/mcp/tools/{tool.id}/config",
            headers={"X-Internal-Secret": _internal_secret},
            json={"business_id": _business_user.id},
        )
        assert r_cfg.status_code == 200
        assert r_cfg.json()["tool_id"] == tool.id
        assert r_cfg.json()["tool_type"] == "postgres"
