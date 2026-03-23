"""BYO (external) MCP: tool discovery via tools/list and tools/call routing (read + write)."""
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.security import get_password_hash
from models.mcp_server import MCPServerConnection
from models.user import User, UserRole
from services.agent_executor import AgentExecutor, _openai_tools_from_mcp


@pytest.fixture
def business_user(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"byo-mcp-{unique}@test.com",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return {"user": user}


def test_openai_tools_external_preserves_remote_input_schema():
    tools = _openai_tools_from_mcp(
        [
            {
                "name": "byo_2_write_row",
                "description": "Writes a row",
                "source": "external",
                "input_schema": {
                    "type": "object",
                    "properties": {"table": {"type": "string"}, "row": {"type": "object"}},
                    "required": ["table"],
                },
            }
        ]
    )
    assert tools[0]["function"]["name"] == "byo_2_write_row"
    assert "table" in tools[0]["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_invoke_mcp_tool_routes_byo_to_external_call(db_session, business_user):
    from services import agent_executor as ae

    u = business_user["user"]
    conn = MCPServerConnection(
        user_id=u.id,
        name="Remote",
        base_url="https://remote-mcp.example.com",
        endpoint_path="/mcp",
        auth_type="none",
        encrypted_credentials=None,
        is_active=True,
    )
    db_session.add(conn)
    db_session.commit()
    db_session.refresh(conn)

    ex = AgentExecutor(db_session)
    fn = f"byo_{conn.id}_insert"
    routing = {
        fn: {"source": "external", "connection_id": conn.id, "external_tool_name": "insert_record"},
    }
    with patch.object(ae, "mcp_call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"content": [{"type": "text", "text": "inserted"}]}
        out = await ex._invoke_mcp_tool(u.id, fn, {"table": "t"}, routing)
    assert "inserted" in out
    assert mock_call.await_args.kwargs["tool_name"] == "insert_record"
    assert mock_call.await_args.kwargs["base_url"] == "https://remote-mcp.example.com"


@pytest.mark.asyncio
async def test_invoke_mcp_tool_unknown_name_returns_error():
    mock_session = MagicMock()
    ex = AgentExecutor(mock_session)
    out = await ex._invoke_mcp_tool(1, "missing_tool", {}, {})
    assert "Unknown tool" in out


@pytest.mark.asyncio
async def test_get_available_mcp_tools_async_expands_byo_tools():
    from services import agent_executor as ae
    from models.mcp_server import MCPServerConnection, MCPToolConfig

    conn_row = MCPServerConnection(
        user_id=42,
        name="Remote",
        base_url="https://mcp.example.com",
        endpoint_path="/mcp",
        auth_type="none",
        encrypted_credentials=None,
        is_active=True,
    )
    conn_row.id = 5

    def _query_side_effect(model):
        m = MagicMock()
        if model is MCPToolConfig:
            m.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []
        elif model is MCPServerConnection:
            m.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [conn_row]
        else:
            m.filter.return_value.all.return_value = []
        return m

    mock_session = MagicMock()
    mock_session.query.side_effect = _query_side_effect
    ex = AgentExecutor(mock_session)
    fake_list = AsyncMock(
        return_value={
            "tools": [
                {"name": "read_doc", "description": "Read", "inputSchema": {"type": "object", "properties": {}}},
                {"name": "write_doc", "description": "Write", "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}},
            ]
        }
    )
    with patch.object(ae, "mcp_list_tools", fake_list):
        tools = await ex._get_available_mcp_tools_async(42, platform_tool_ids=[], connection_ids=[5])
    names = [t["name"] for t in tools]
    assert any(n.startswith("byo_5_") for n in names)
    byo = [t for t in tools if t.get("source") == "external"]
    assert len(byo) == 2
    assert {t["external_tool_name"] for t in byo} == {"read_doc", "write_doc"}
    fake_list.assert_called_once()
