"""
Unit tests for MCP HTTP client (services.mcp_client).

Validates compatibility with:
- MCP streamable HTTP (2025-11-25): single endpoint, POST, response as JSON or SSE
- Legacy HTTP+SSE (2024-11-05): POST to endpoint, SSE message events
- Open-source servers that return application/json (e.g. many self-hosted) or text/event-stream (e.g. PageIndex).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.mcp_client import _parse_sse_to_json, call_mcp_server


# ---------- SSE parsing: works with all MCP servers that use SSE ----------


class TestParseSseToJson:
    """_parse_sse_to_json extracts JSON from SSE body (PageIndex, legacy HTTP+SSE)."""

    def test_single_data_line(self):
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}\n\n'
        out = _parse_sse_to_json(body)
        assert out["jsonrpc"] == "2.0"
        assert out["id"] == 1
        assert out["result"]["protocolVersion"] == "2024-11-05"

    def test_first_event_ends_at_blank_line(self):
        # Second event is ignored; we use only the first event's data
        body = 'event: message\ndata: {"first": true}\n\nevent: message\ndata: {"second": true}\n\n'
        out = _parse_sse_to_json(body)
        assert out == {"first": True}

    def test_empty_data_line_ignored(self):
        body = 'event: message\ndata: \ndata: {"ok": true}\n\n'
        out = _parse_sse_to_json(body)
        assert out == {"ok": True}

    def test_no_data_raises(self):
        with pytest.raises(ValueError, match="No data field"):
            _parse_sse_to_json("event: message\n\n")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No data field"):
            _parse_sse_to_json("")


# ---------- call_mcp_server: JSON response (many MCP servers) ----------


@pytest.mark.asyncio
async def test_call_mcp_server_json_response():
    """Server returns Content-Type: application/json (common for self-hosted MCP)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"test"}}}'
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "test"}}}
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        result = await call_mcp_server(
            base_url="https://mcp.example.com",
            method="initialize",
            params={"protocolVersion": "2024-11-05"},
        )
    assert result["result"]["serverInfo"]["name"] == "test"


@pytest.mark.asyncio
async def test_call_mcp_server_sse_response():
    """Server returns Content-Type: text/event-stream (e.g. PageIndex)."""
    sse_body = (
        'event: message\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","serverInfo":{"name":"pageindex-mcp"}}}\n\n'
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = sse_body
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        result = await call_mcp_server(
            base_url="https://api.pageindex.ai",
            method="initialize",
            params={"protocolVersion": "2024-11-05"},
            endpoint_path="/mcp",
        )
    assert result["result"]["serverInfo"]["name"] == "pageindex-mcp"


@pytest.mark.asyncio
async def test_call_mcp_server_sends_accept_and_protocol_version():
    """Client sends Accept and MCP-Protocol-Version per MCP spec."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"jsonrpc":"2.0","id":1,"result":{}}'
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
    mock_response.raise_for_status = MagicMock()

    post_called_with = {}

    async def capture_post(*args, **kwargs):
        post_called_with["args"] = args
        post_called_with["kwargs"] = kwargs
        return mock_response

    mock_http = MagicMock()
    mock_http.post = AsyncMock(side_effect=capture_post)
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        await call_mcp_server(base_url="https://mcp.example.com", method="initialize", params={})

    headers = post_called_with["kwargs"]["headers"]
    assert "application/json" in headers["Accept"] and "text/event-stream" in headers["Accept"]
    assert headers.get("MCP-Protocol-Version") == "2024-11-05"


@pytest.mark.asyncio
async def test_call_mcp_server_base_url_includes_path_no_double_slash():
    """When base_url already ends with endpoint path, do not append again (e.g. https://api.pageindex.ai/mcp)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"jsonrpc":"2.0","id":1,"result":{}}'
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    post_mock = AsyncMock(return_value=mock_response)
    mock_http.post = post_mock
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        await call_mcp_server(
            base_url="https://api.pageindex.ai/mcp",
            method="initialize",
            params={},
            endpoint_path="/mcp",
        )
        # Should POST to https://api.pageindex.ai/mcp, not .../mcp/mcp
        call_url = post_mock.call_args[0][0]
        assert call_url == "https://api.pageindex.ai/mcp"


# ---------- Authentication: bearer, api_key, basic ----------


def _capture_post(capture_dict):
    async def _post(*args, **kwargs):
        capture_dict["kwargs"] = kwargs
        r = MagicMock()
        r.status_code = 200
        r.text = '{"jsonrpc":"2.0","id":1,"result":{}}'
        r.headers = {"content-type": "application/json"}
        r.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        r.raise_for_status = MagicMock()
        return r
    return _post


@pytest.mark.asyncio
async def test_call_mcp_server_api_key_auth_sent_as_bearer():
    """API key auth: sent as Authorization Bearer, value trimmed."""
    captured = {}
    mock_http = MagicMock()
    mock_http.post = AsyncMock(side_effect=_capture_post(captured))
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        await call_mcp_server(
            base_url="https://mcp.example.com",
            method="initialize",
            params={},
            auth_type="api_key",
            credentials={"api_key": "  my-key-123  "},
        )
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer my-key-123"


@pytest.mark.asyncio
async def test_call_mcp_server_basic_auth():
    """Basic auth: username:password base64-encoded, trimmed."""
    import base64
    captured = {}
    mock_http = MagicMock()
    mock_http.post = AsyncMock(side_effect=_capture_post(captured))
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        await call_mcp_server(
            base_url="https://mcp.example.com",
            method="initialize",
            params={},
            auth_type="basic",
            credentials={"username": "  alice  ", "password": "  secret  "},
        )
    auth = captured["kwargs"]["headers"]["Authorization"]
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth[6:]).decode()
    assert decoded == "alice:secret"  # username and password are trimmed


@pytest.mark.asyncio
async def test_call_mcp_server_basic_empty_credentials_no_header():
    """Basic auth with both username and password empty: no Authorization header set."""
    captured = {}
    mock_http = MagicMock()
    mock_http.post = AsyncMock(side_effect=_capture_post(captured))
    mock_get = AsyncMock(return_value=mock_http)
    with patch("services.mcp_client._get_async_http_client", mock_get):
        await call_mcp_server(
            base_url="https://mcp.example.com",
            method="initialize",
            params={},
            auth_type="basic",
            credentials={"username": "", "password": ""},
        )
    assert "Authorization" not in captured["kwargs"]["headers"]
